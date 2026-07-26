"""Memory Graph 二期测试：自动写边 + 召回注入

零 mock 真 sqlite 端到端（沿用 test_cluster_persistence 的 repo_db 基建模式：
真引擎真建表，仅把 repo 基类 session 工厂指向测试库）。
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

    from backend.models.base import Base
    import backend.models  # noqa: F401 注册全模型

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    with patch("backend.repositories.base.AsyncSessionLocal", SessionLocal):
        yield SessionLocal
    asyncio.run(engine.dispose())


def _repo():
    from backend.repositories.memory_graph_repo import AsyncMemoryGraphRepository

    return AsyncMemoryGraphRepository()


def _node(title: str, *, kind="knowledge", tags=None, content="", user_id=None):
    return _repo().add_node({
        "user_id": user_id,
        "kind": kind,
        "title": title,
        "content": content,
        "tags": tags or [],
        "source": "agent",
    })


def test_auto_link_by_shared_tags(repo_db):
    async def _run():
        uid = uuid.uuid4()
        a = await _node("Takton 部署规范", tags=["takton", "deploy"], user_id=uid)
        b = await _node("Takton 回滚流程", tags=["takton", "deploy"], user_id=uid)
        c = await _node("完全不相关的菜谱", tags=["cooking"], user_id=uid)

        edges = await _repo().auto_link(a)
        linked_ids = {e.to_id for e in edges}
        assert b.id in linked_ids  # 共同 tag ×2 + title 词重叠，达标
        assert c.id not in linked_ids  # 零重叠，不建边

        # 重复调用不重复建边
        again = await _repo().auto_link(a)
        assert all(e.to_id != b.id for e in again)

    asyncio.run(_run())


def test_auto_link_by_title_containment(repo_db):
    async def _run():
        a = await _node("Takton 部署", tags=[])
        b = await _node("Takton 部署规范 v2", tags=[])
        edges = await _repo().auto_link(a)
        assert any(e.to_id == b.id for e in edges)

    asyncio.run(_run())


def test_auto_link_respects_max_edges(repo_db):
    async def _run():
        uid = uuid.uuid4()
        for i in range(6):
            await _node(f"关联候选 {i}", tags=["shared-tag", "takton"], user_id=uid)
        new = await _node("新节点", tags=["shared-tag", "takton"], user_id=uid)
        edges = await _repo().auto_link(new, max_edges=3)
        assert len(edges) == 3

    asyncio.run(_run())


def test_auto_link_user_isolation(repo_db):
    """不同 user 的节点不参与自动写边"""
    async def _run():
        uid_a, uid_b = uuid.uuid4(), uuid.uuid4()
        mine = await _node("我的记忆", tags=["shared"], user_id=uid_a)
        other = await _node("别人的记忆", tags=["shared"], user_id=uid_b)
        edges = await _repo().auto_link(mine)
        assert all(e.to_id != other.id for e in edges)

    asyncio.run(_run())


def test_remember_tool_reports_auto_link(repo_db):
    async def _run():
        from backend.tools.builtins.memory_tools import MemoryGraphTool

        tool = MemoryGraphTool()
        await tool.execute(
            action="remember", kind="knowledge",
            title="Takton 压测口径", content="前端真实发起", tags=["takton"],
        )
        out = await tool.execute(
            action="remember", kind="experience",
            title="Takton 压测排障", content="压测发现的问题", tags=["takton"],
        )
        assert "[remembered]" in out
        assert "自动关联 1 条" in out

    asyncio.run(_run())


def test_auto_recall_injects_into_context(repo_db):
    """build_messages：user_input 命中记忆时注入召回块，不命中回退静态提示

    注：记忆属于 Volatile 层。T4（prompt caching）起，Volatile 不再并入
    messages[0] —— 它随记忆写入而变，会让整段可缓存前缀失配。改挂 messages
    尾部后内容不变、位置改变，故这里断言「出现在 messages 里」而非固定下标。
    """
    def _all_system_text(messages):
        return "\n".join(
            str(m.get("content") or "")
            for m in messages
            if m.get("role") == "system"
        )

    async def _run():
        await _node("Xray 出站配置", content="SNI 必须与 outbounds serverName 同步", tags=["xray"])

        from backend.agent.context import ContextManager

        builder = ContextManager(ctx_item_repo=None)
        messages, _items, _tokens = await builder.build_messages(
            session_id=uuid.uuid4(),
            user_input="xray 出站又断了",
            history=[],
            tools_enabled=[],
            model="test-model",
        )
        system_text = _all_system_text(messages)
        assert "召回的相关长期记忆" in system_text
        assert "Xray 出站配置" in system_text
        # 记忆必须留在可缓存前缀之外
        assert "Xray 出站配置" not in messages[0]["content"]

        # 不命中 → 回退静态提示
        builder2 = ContextManager(ctx_item_repo=None)
        messages2, _i2, _t2 = await builder2.build_messages(
            session_id=uuid.uuid4(),
            user_input="zzz-no-match-qqq",
            history=[],
            tools_enabled=[],
            model="test-model",
        )
        assert "条长期记忆" in _all_system_text(messages2)

    asyncio.run(_run())
