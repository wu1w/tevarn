"""Identity Memory 入 RAG 测试（Alpha Review #4）。

零 mock 红线：真实 SQLite + 真 IdentityRegistry/Dispatcher。
向量栈测试走 best-effort 降级路径（本地模式 / Qdrant 不可达）——
验证「RAG 不可用不阻塞记忆写入」与「检索式注入回落」两条红线，
而非伪造 embedding/qdrant 响应。
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.kernel import AgentKernel
from backend.kernel.audit_store import AuditEventStore
from backend.kernel.dispatcher import WorkforceDispatcher
from backend.kernel.identity import IdentityRegistry
from backend.kernel.inbox import InboxService


@pytest.fixture()
def wfx(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/rag.db", future=True)
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    from backend.models.base import Base
    import backend.models  # noqa: F401

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    store = AuditEventStore(str(tmp_path / "events.jsonl"))
    kernel = AgentKernel(audit_store=store)
    registry = IdentityRegistry(kernel, SessionLocal)
    inbox = InboxService(kernel, SessionLocal, max_pending=10)
    yield {
        "kernel": kernel, "registry": registry, "inbox": inbox,
        "SessionLocal": SessionLocal, "store": store,
    }
    asyncio.run(engine.dispose())


def _run(coro):
    return asyncio.run(coro)


# ── 接口默认实现（NullRAG 继承的安全 no-op）────────────────────


def test_null_rag_identity_memory_interface_safe() -> None:
    from backend.services.rag.null_impl import NullRAGService

    rag = NullRAGService()

    async def go():
        assert await rag.upsert_identity_memory(
            entry_id="e1", identity_id="i1", kind="persona",
            content="谨慎", version=1,
        ) is False
        assert await rag.delete_identity_memory("e1") is False
        assert await rag.search_identity_memory("query", "i1") == []

    _run(go())


# ── 本地模式：记忆写入不被 RAG 阻塞 ─────────────────────────────


def test_add_memory_local_mode_not_blocked(wfx) -> None:
    """本地模式（无向量栈）：add_memory 正常落库，索引静默跳过。"""
    async def go():
        reg = wfx["registry"]
        ident = await reg.create("本地员工", capabilities=["file_read"])
        entry = await reg.add_memory(ident.id, "persona", "严谨细致的代码审查者")
        assert entry.id is not None

        entries = await reg.current_memory(ident.id)
        assert len(entries) == 1
        assert entries[0].content == "严谨细致的代码审查者"

    _run(go())


def test_supersede_memory_local_mode_version_chain(wfx) -> None:
    """本地模式 supersede：版本链正常，旧版被取代。"""
    async def go():
        reg = wfx["registry"]
        ident = await reg.create("进化员工", capabilities=["file_read"])
        old = await reg.add_memory(ident.id, "methodology", "先写实现再补测试")
        new = await reg.supersede_memory(old.id, "先写测试再写实现", approved_by="wuyw")
        assert new.version == old.version + 1

        current = await reg.current_memory(ident.id)
        assert len(current) == 1
        assert current[0].content == "先写测试再写实现"

    _run(go())


# ── 检索式注入：本地模式回落 ────────────────────────────────────


def test_retrieve_identity_memory_local_mode_returns_none(wfx) -> None:
    """本地模式（use_vector_rag=False）：检索返回 None → 调用方回落。"""
    async def go():
        reg, inbox, kernel = wfx["registry"], wfx["inbox"], wfx["kernel"]
        ident = await reg.create("检索员工", capabilities=["file_read"])
        disp = WorkforceDispatcher(kernel, inbox, reg, wfx["SessionLocal"])
        out = await disp._retrieve_identity_memory(ident, "审查这个模块")
        assert out is None

    _run(go())


def test_build_memory_block_small_full_inject(wfx) -> None:
    """条目 ≤ 阈值：全量硬注入。"""
    async def go():
        reg, inbox, kernel = wfx["registry"], wfx["inbox"], wfx["kernel"]
        ident = await reg.create("小记忆员工", capabilities=["file_read"])
        await reg.add_memory(ident.id, "persona", "谨慎")
        await reg.add_memory(ident.id, "duty", "负责代码审查")
        entries = await reg.current_memory(ident.id)

        disp = WorkforceDispatcher(kernel, inbox, reg, wfx["SessionLocal"])
        header, text = await disp._build_memory_block(ident, "审查 API", entries)
        assert "身份记忆" in header
        assert "[persona] 谨慎" in text
        assert "[duty] 负责代码审查" in text

    _run(go())


def test_build_memory_block_large_falls_back_truncated(wfx) -> None:
    """条目 > 阈值 + 检索不可用（本地模式）：回落全量截断（≤4000）。"""
    async def go():
        reg, inbox, kernel = wfx["registry"], wfx["inbox"], wfx["kernel"]
        ident = await reg.create("大记忆员工", capabilities=["file_read"])
        for i in range(12):  # 超 full_inject_max=8
            await reg.add_memory(ident.id, "experience", f"经验条目 {i} " + "x" * 500)
        entries = await reg.current_memory(ident.id)
        assert len(entries) == 12

        disp = WorkforceDispatcher(kernel, inbox, reg, wfx["SessionLocal"])
        header, text = await disp._build_memory_block(ident, "做点什么", entries)
        assert header == "## 你的身份记忆（长期人格/职责/方法论）"  # 回落=全量标题
        assert len(text) <= 4000  # 截断保护
        assert "[experience]" in text  # 保头——前面的条目在

    _run(go())


# ── Qdrant 不可达：best-effort 不抛 ────────────────────────────


def test_qdrant_rag_identity_memory_unreachable_degrades() -> None:
    """Qdrant/embedding 不可达：三方法返回安全值而非抛异常
    （生产 RAG 故障时记忆系统必须继续可用）。"""
    from backend.services.rag.qdrant_impl import QdrantRAGService

    rag = QdrantRAGService.__new__(QdrantRAGService)
    rag.qdrant_url = "http://127.0.0.1:1"  # 不可达
    rag._ensured_collections = set()
    rag._diagnostics = None

    class _BrokenEmbedding:
        async def embed_query(self, q):
            raise ConnectionError("embedding service down")

    rag.embedding_service = _BrokenEmbedding()

    async def go():
        ok = await rag.upsert_identity_memory(
            entry_id="e1", identity_id="i1", kind="persona",
            content="x", version=1,
        )
        assert ok is False
        assert await rag.delete_identity_memory("e1") is False
        assert await rag.search_identity_memory("q", "i1") == []

    _run(go())
