"""0.5/0.6 续开发：E4 统一停止 · F2 全局并发 · 日报已读 · cancel 工单。"""

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
def env(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/stop.db", future=True)
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
    inbox = InboxService(kernel, SessionLocal, max_pending=50)
    yield {
        "kernel": kernel,
        "registry": registry,
        "inbox": inbox,
        "SessionLocal": SessionLocal,
        "store": store,
        "monkeypatch": monkeypatch,
    }
    asyncio.run(engine.dispose())


def _run(coro):
    return asyncio.run(coro)


def test_inbox_cancel_no_retry(env) -> None:
    async def go():
        reg, inbox = env["registry"], env["inbox"]
        ident = await reg.create("stop-worker", role="qa", capabilities=["file_rw"])
        item = await inbox.enqueue(ident.id, "to cancel", source="api")
        claimed = await inbox.claim_next()
        assert claimed is not None
        cancelled = await inbox.cancel(claimed.id, reason="user stop")
        assert cancelled is not None
        assert cancelled.status == "cancelled"
        # 不会再被 claim
        nxt = await inbox.claim_next()
        assert nxt is None
        kinds = [e.kind for e in env["kernel"].events()]
        assert "inbox_cancelled" in kinds

    _run(go())


def test_dispatcher_cancel_job_while_running(env) -> None:
    """E4：在跑工单 cancel → loop/task 停 · process killed · 工单 cancelled。"""

    async def go():
        reg, inbox, kernel = env["registry"], env["inbox"], env["kernel"]
        ident = await reg.create("runner", capabilities=["file_read"])
        item = await inbox.enqueue(ident.id, "long job")

        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_executor(identity, it, proc_id, k):
            started.set()
            await release.wait()
            return "should not finish"

        disp = WorkforceDispatcher(
            kernel, inbox, reg, env["SessionLocal"], executor=slow_executor
        )
        # 非 wait 派发，后台跑
        n = await disp.tick(wait=False)
        assert n == 1
        await asyncio.wait_for(started.wait(), timeout=2.0)

        result = await disp.cancel_job(item_id=str(item.id), reason="test stop")
        assert result["ok"] is True
        assert result["task_cancelled"] or result["inbox_cancelled"]

        # 放行 executor（若仍挂着）以免泄漏
        release.set()
        await asyncio.sleep(0.15)

        rows = await inbox.list_items(status="cancelled", limit=10)
        assert any(str(r.id) == str(item.id) for r in rows)

        pid = result.get("process_id")
        if pid:
            proc = kernel.get_process(pid)
            if proc is not None:
                assert proc.state == "killed"

    _run(go())


def test_global_concurrency_cap(env) -> None:
    """F2：全局并发上限限制同 tick 派发数。"""

    async def go():
        from backend.core import config as cfg

        env["monkeypatch"].setattr(
            cfg.settings, "agent_dispatcher_max_global_concurrent", 2, raising=False
        )

        reg, inbox, kernel = env["registry"], env["inbox"], env["kernel"]
        workers = []
        for i in range(4):
            workers.append(
                await reg.create(f"w{i}", capabilities=["file_read"])
            )
            await inbox.enqueue(workers[-1].id, f"job {i}")

        gate = asyncio.Event()

        async def hold_executor(identity, it, proc_id, k):
            await gate.wait()
            return "ok"

        disp = WorkforceDispatcher(
            kernel, inbox, reg, env["SessionLocal"], executor=hold_executor
        )
        n1 = await disp.tick(wait=False)
        assert n1 == 2  # 全局 cap=2
        assert len(disp._busy) == 2

        # 仍有 pending，但 busy 满了再 tick 不派
        n2 = await disp.tick(wait=False)
        assert n2 == 0

        gate.set()
        # 等第一批完成
        for t in list(disp._item_tasks.values()):
            try:
                await asyncio.wait_for(asyncio.shield(t), timeout=2.0)
            except Exception:
                pass
        await asyncio.sleep(0.05)

        n3 = await disp.tick(wait=True)
        assert n3 == 2  # 剩余 2 单

    _run(go())


def test_seed_template_crew_idempotent(env) -> None:
    async def go():
        from backend.scripts.seed_template_crew import seed_template_crew

        reg = env["registry"]
        r1 = await seed_template_crew(reg)
        assert r1["ok"] is True
        assert len(r1["created"]) == 3
        r2 = await seed_template_crew(reg)
        assert len(r2["created"]) == 0
        assert len(r2["skipped"]) == 3

    _run(go())
