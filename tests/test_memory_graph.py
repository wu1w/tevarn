"""Phase 1 Memory Graph MVP 测试

真 sqlite 端到端：模型建表 → repo → memory_graph 工具 全链路。
覆盖：remember/recall(关键词+kind+命中自增)/link/forget/subgraph、
边界（非法 kind/缺 title/节点不存在/未知 action）、表自动创建。
"""
import asyncio
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest.fixture()
def repo_db(tmp_path):
    """真 sqlite + 全表创建，patch 到 repo 基类的 session 工厂"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/mg.db", future=True)
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    import backend.models  # noqa: F401 注册全模型
    from backend.models.base import Base

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    with patch("backend.repositories.base.AsyncSessionLocal", SessionLocal):
        yield SessionLocal
    asyncio.run(engine.dispose())


def test_tables_created_by_init_db(repo_db):
    """memory_nodes / memory_edges 经 create_all 落地"""
    from backend.repositories.memory_graph_repo import AsyncMemoryGraphRepository

    async def _run():
        repo = AsyncMemoryGraphRepository()
        node = await repo.add_node({
            "kind": "decision", "title": "用 bwrap 做沙箱", "content": "不用 docker",
        })
        assert node.id is not None
        got = await repo.get_node(node.id)
        assert got.title == "用 bwrap 做沙箱"

    asyncio.run(_run())


def test_remember_and_recall(repo_db):
    from backend.tools.builtins.memory_tools import MemoryGraphTool

    async def _run():
        tool = MemoryGraphTool()
        r1 = await tool.execute(
            action="remember", kind="preference", title="提交信息用中文",
            content="git commit message 一律中文", tags=["git"], _session_id="s1",
        )
        assert r1.startswith("[remembered] preference")

        await tool.execute(action="remember", kind="knowledge", title="发版流程", content="先 bump 再 tag")
        # 关键词召回
        out = await tool.execute(action="recall", query="中文")
        assert "提交信息用中文" in out
        # kind 过滤
        out2 = await tool.execute(action="recall", kind="knowledge")
        assert "发版流程" in out2 and "提交信息用中文" not in out2
        # 无匹配
        assert "无匹配记忆" in await tool.execute(action="recall", query="不存在的词xyz")

    asyncio.run(_run())


def test_recall_bumps_hit_count(repo_db):
    from backend.repositories.memory_graph_repo import AsyncMemoryGraphRepository

    async def _run():
        repo = AsyncMemoryGraphRepository()
        node = await repo.add_node({"kind": "experience", "title": "OOM 教训", "content": "batch 别超 32"})
        await repo.recall(query="OOM")
        got = await repo.get_node(node.id)
        assert got.hit_count == 1

    asyncio.run(_run())


def test_link_and_subgraph(repo_db):
    from backend.tools.builtins.memory_tools import MemoryGraphTool

    async def _run():
        from backend.repositories.memory_graph_repo import AsyncMemoryGraphRepository

        repo = AsyncMemoryGraphRepository()
        tool = MemoryGraphTool()
        a = await repo.add_node({"kind": "decision", "title": "选 bwrap"})
        b = await repo.add_node({"kind": "knowledge", "title": "bwrap 用法"})

        out = await tool.execute(action="link", from_id=str(a.id), to_id=str(b.id), relation="supports")
        assert "[linked]" in out and "supports" in out

        sg = await tool.execute(action="subgraph", node_id=str(a.id))
        assert "选 bwrap" in sg and "bwrap 用法" in sg and "supports" in sg

        # 节点不存在
        err = await tool.execute(action="link", from_id=str(a.id), to_id=str(uuid.uuid4()))
        assert err.startswith("[Error] to 节点不存在")

    asyncio.run(_run())


def test_forget_cascades(repo_db):
    from backend.repositories.memory_graph_repo import AsyncMemoryGraphRepository
    from backend.tools.builtins.memory_tools import MemoryGraphTool

    async def _run():
        repo = AsyncMemoryGraphRepository()
        tool = MemoryGraphTool()
        a = await repo.add_node({"kind": "knowledge", "title": "A"})
        b = await repo.add_node({"kind": "knowledge", "title": "B"})
        await tool.execute(action="link", from_id=str(a.id), to_id=str(b.id))

        out = await tool.execute(action="forget", node_id=str(a.id))
        assert out.startswith("[forgot]")
        assert await repo.get_node(a.id) is None
        assert await repo.edges_of(b.id) == []  # 级联删边

        assert "不存在" in await tool.execute(action="forget", node_id=str(a.id))

    asyncio.run(_run())


def test_tool_input_validation(repo_db):
    from backend.tools.builtins.memory_tools import MemoryGraphTool

    async def _run():
        tool = MemoryGraphTool()
        assert "[Error] kind" in await tool.execute(action="remember", kind="diary", title="x")
        assert "[Error] title" in await tool.execute(action="remember", kind="knowledge")
        assert "未知 action" in await tool.execute(action="dance")

    asyncio.run(_run())


def test_remember_with_link_to(repo_db):
    from backend.repositories.memory_graph_repo import AsyncMemoryGraphRepository
    from backend.tools.builtins.memory_tools import MemoryGraphTool

    async def _run():
        repo = AsyncMemoryGraphRepository()
        tool = MemoryGraphTool()
        parent = await repo.add_node({"kind": "knowledge", "title": "Takton 架构"})
        out = await tool.execute(
            action="remember", kind="decision", title="单 main 分支",
            content="不用 feature 分支", link_to=str(parent.id),
        )
        assert "已关联到" in out
        edges = await repo.edges_of(parent.id)
        assert len(edges) == 1 and edges[0].relation == "related_to"

    asyncio.run(_run())
