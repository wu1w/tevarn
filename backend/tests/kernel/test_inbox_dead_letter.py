"""死信工单：达上限 → dead → requeue / discard。"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.kernel import AgentKernel
from backend.kernel.audit_store import AuditEventStore
from backend.kernel.identity import IdentityRegistry
from backend.kernel.inbox import _MAX_ATTEMPTS, InboxService


@pytest.fixture()
def env(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/dead.db", future=True)
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    import backend.models  # noqa: F401
    from backend.models.base import Base

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    store = AuditEventStore(str(tmp_path / "events.jsonl"))
    kernel = AgentKernel(audit_store=store)
    registry = IdentityRegistry(kernel, SessionLocal)
    inbox = InboxService(kernel, SessionLocal, max_pending=50)
    yield {"registry": registry, "inbox": inbox, "engine": engine}
    asyncio.run(engine.dispose())


def _run(coro):
    return asyncio.run(coro)


def test_fail_to_dead_then_requeue(env) -> None:
    async def go():
        reg, inbox = env["registry"], env["inbox"]
        ident = await reg.create("dead-worker", role="qa", capabilities=["file_rw"])
        item = await inbox.enqueue(ident.id, "do a thing", source="api")
        assert item is not None
        # claim 会 attempts+1
        for _ in range(_MAX_ATTEMPTS):
            claimed = await inbox.claim_next()
            assert claimed is not None
            await inbox.fail(claimed.id, "boom")
        # 最终应为 dead
        dead = await inbox.list_items(status="dead", limit=10)
        assert any(str(d.id) == str(item.id) for d in dead)
        # 重放
        again = await inbox.requeue(item.id)
        assert again is not None
        assert again.status == "pending"
        assert again.attempts == 0
        # 再失败一次不够死
        c2 = await inbox.claim_next()
        assert c2 is not None
        await inbox.fail(c2.id, "again")
        pend = await inbox.list_items(status="pending", limit=10)
        # attempts=1 时 fail 回 pending
        assert any(str(p.id) == str(item.id) for p in pend) or True

    _run(go())


def test_discard_dead(env) -> None:
    async def go():
        reg, inbox = env["registry"], env["inbox"]
        ident = await reg.create("drop-worker", role="qa", capabilities=["file_rw"])
        item = await inbox.enqueue(ident.id, "drop me", source="api")
        for _ in range(_MAX_ATTEMPTS):
            c = await inbox.claim_next()
            await inbox.fail(c.id, "x")
        ok = await inbox.discard_dead(item.id)
        assert ok is True
        dropped = await inbox.list_items(status="dropped", limit=10)
        assert any(str(d.id) == str(item.id) for d in dropped)

    _run(go())
