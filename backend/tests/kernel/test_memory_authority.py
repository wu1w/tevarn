"""记忆读取优先级：Identity Memory 进入工单 prompt。"""

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
def env(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/mem.db", future=True)
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    from backend.models.base import Base
    import backend.models  # noqa: F401

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    store = AuditEventStore(str(tmp_path / "e.jsonl"))
    kernel = AgentKernel(audit_store=store)
    registry = IdentityRegistry(kernel, SessionLocal)
    inbox = InboxService(kernel, SessionLocal, max_pending=20)
    yield {
        "registry": registry,
        "inbox": inbox,
        "kernel": kernel,
        "SessionLocal": SessionLocal,
        "engine": engine,
    }
    asyncio.run(engine.dispose())


def test_identity_memory_in_workforce_prompt(env) -> None:
    async def go():
        reg, inbox, kernel = env["registry"], env["inbox"], env["kernel"]
        ident = await reg.create(
            "记忆工",
            role="research",
            capabilities=["file_rw", "web_search"],
        )
        await reg.append_memory(
            ident.id,
            "persona",
            "说话简洁，先结论后证据",
            source="system",
            approved_by="test",
        )
        await reg.append_memory(
            ident.id,
            "duty",
            "负责调研并写三句话摘要",
            source="system",
            approved_by="test",
        )
        item = await inbox.enqueue(ident.id, "调研 foo", source="api")
        assert item is not None

        captured: dict = {}

        async def ex(identity, it, proc_id, k):
            # 通过 dispatcher 内部构造的 prompt 不可直接拿；用 _build_memory_block
            return "ok"

        disp = WorkforceDispatcher(
            kernel, inbox, reg, env["SessionLocal"], executor=ex
        )
        header, body = await disp._build_memory_block(
            ident,
            "调研 foo",
            await reg.current_memory(ident.id),
        )
        assert "身份记忆" in header or "记忆" in header
        assert "简洁" in body or "调研" in body or "duty" in body.lower() or "职责" in body or "persona" in body or "说话" in body
        assert "调研并写" in body or "摘要" in body or "duty" in body

        await disp.tick(wait=True)
        done = await inbox.list_items(status="done")
        assert len(done) >= 1

    asyncio.run(go())
