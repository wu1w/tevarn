"""回归：审计循环确认的真 bug（修前应红，修后全绿）。"""
from __future__ import annotations

import asyncio
import time
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.kernel import AgentKernel, BudgetExceededError
from backend.kernel.audit_store import AuditEventStore
from backend.kernel.evolution_engine import EvolutionEngine
from backend.kernel.identity import IdentityRegistry
from backend.kernel.inbox import InboxService
from backend.kernel.kernel import EscalationRequest


@pytest.fixture()
def wf(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/bugfix.db", future=True)
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
    evo = EvolutionEngine(kernel, registry, SessionLocal)
    yield {
        "kernel": kernel,
        "registry": registry,
        "inbox": inbox,
        "engine": evo,
        "SessionLocal": SessionLocal,
    }
    asyncio.run(engine.dispose())


def _run(coro):
    return asyncio.run(coro)


def test_evolution_caps_adjust_is_identity_scoped(wf) -> None:
    """P0-4：身份 B 的分析不得吃到身份 A 的 escalation 获批统计。"""

    async def go():
        reg, eng, kernel = wf["registry"], wf["engine"], wf["kernel"]
        a = await reg.create("员工A", capabilities=["file_read"], user_id=uuid.uuid4())
        b = await reg.create("员工B", capabilities=["file_read"], user_id=uuid.uuid4())
        # 仅给 A 造 3 次 browser 获批（挂在 A 的 process + identity 元数据）
        proc_a = await kernel.create_process(
            f"wf:{a.name}",
            capabilities=["file_read"],
            meta={"identity_id": str(a.id)},
        )
        for _ in range(3):
            kernel._emit(
                "escalation_approved",
                proc_a.id,
                {
                    "escalation_id": "x",
                    "capabilities": ["browser"],
                    "resolved_by": "boss",
                    "identity_id": str(a.id),
                },
            )
        # B 无任何获批
        props_b = await eng.analyze(b.id)
        caps_b = [p for p in props_b if p.kind == "caps_adjust"]
        assert caps_b == [], "跨身份污染：B 不应因 A 的获批产生 caps_adjust"

        props_a = await eng.analyze(a.id)
        caps_a = [p for p in props_a if p.kind == "caps_adjust"]
        assert len(caps_a) == 1
        assert "browser" in (caps_a[0].payload or {}).get("add_capabilities", [])

    _run(go())


def test_evolution_tool_deprecate_is_identity_scoped(wf) -> None:
    """P0-4：mediation 拒绝率统计必须按身份隔离。"""

    async def go():
        reg, eng, kernel = wf["registry"], wf["engine"], wf["kernel"]
        a = await reg.create("员工A2", capabilities=["shell", "file_read"])
        b = await reg.create("员工B2", capabilities=["shell", "file_read"])
        proc_a = await kernel.create_process(
            f"wf:{a.name}",
            capabilities=["shell", "file_read"],
            meta={"identity_id": str(a.id)},
        )
        for _ in range(6):
            kernel._emit(
                "mediation",
                proc_a.id,
                {
                    "action": "tool_call",
                    "target": "shell",
                    "allowed": False,
                    "identity_id": str(a.id),
                },
            )
        props_b = await eng.analyze(b.id)
        dep_b = [p for p in props_b if p.kind == "tool_deprecate"]
        assert dep_b == []

    _run(go())


def test_claim_next_atomic_no_double_claim(wf) -> None:
    """P0-2：并发 claim 同一 pending 只能一人成功。"""

    async def go():
        reg, inbox = wf["registry"], wf["inbox"]
        ident = await reg.create("并发员工", capabilities=["file_read"])
        item = await inbox.enqueue(ident.id, "唯一工单", source="api")
        assert item is not None

        results = await asyncio.gather(
            inbox.claim_next(),
            inbox.claim_next(),
            inbox.claim_next(),
        )
        claimed = [r for r in results if r is not None]
        assert len(claimed) == 1
        assert str(claimed[0].id) == str(item.id)
        # 其余必须仍为 claimed 或 none，不能再有第二条 claimed 同 id
        ids = [str(c.id) for c in claimed]
        assert len(set(ids)) == 1

    _run(go())


def test_reclaim_stale_claimed(wf) -> None:
    """P1-5：过期 claimed 可回收。"""

    async def go():
        reg, inbox = wf["registry"], wf["inbox"]
        ident = await reg.create("卡住员工", capabilities=["file_read"])
        item = await inbox.enqueue(ident.id, "会卡住", source="api")
        claimed = await inbox.claim_next()
        assert claimed is not None
        # 伪造超时
        from sqlalchemy import select
        from backend.models.agent_identity import AgentInboxItem

        async with inbox._session_factory() as s:
            row = (
                await s.execute(select(AgentInboxItem).where(AgentInboxItem.id == claimed.id))
            ).scalar_one()
            row.claimed_at = time.time() - 10_000
            await s.commit()

        n = await inbox.reclaim_stale_claims(timeout_seconds=60)
        assert n >= 1
        again = await inbox.claim_next()
        assert again is not None
        assert str(again.id) == str(item.id)

    _run(go())


def test_escalation_hydrate_refreshes_stale_status(wf) -> None:
    """P0-3：内存中的 pending 必须被远端 approved 覆盖。"""

    async def go():
        kernel = wf["kernel"]
        proc = await kernel.create_process("main", capabilities=["file_read"])
        req = await kernel.request_escalation(proc.id, ["browser"], reason="need")
        assert req.status == "pending"
        # 模拟他 worker 已批准：直接污染内存为旧 pending，再 hydrate approved
        kernel._escalations[req.id] = EscalationRequest(
            id=req.id,
            process_id=req.process_id,
            capabilities=req.capabilities,
            reason=req.reason,
            status="pending",
            created_at=req.created_at,
        )
        kernel.hydrate_escalation(
            {
                "id": req.id,
                "process_id": req.process_id,
                "capabilities": list(req.capabilities),
                "reason": req.reason,
                "status": "approved",
                "created_at": req.created_at,
                "resolved_at": time.time(),
                "resolved_by": "other-worker",
                "target": "process",
            }
        )
        assert kernel._escalations[req.id].status == "approved"

    _run(go())


def test_parent_budget_reserved_on_child_create(wf) -> None:
    """P1-6：子进程占用预算后父剩余减少，不能双花。"""

    async def go():
        kernel = wf["kernel"]
        parent = await kernel.create_process("boss", capabilities=["*"], token_budget=10_000)
        c1 = await kernel.create_process(
            "child1", parent_id=parent.id, capabilities=["file_read"], token_budget=8_000
        )
        parent2 = kernel.get_process(parent.id)
        assert parent2 is not None
        assert parent2.budget_remaining is not None
        assert parent2.budget_remaining <= 2_000
        with pytest.raises((BudgetExceededError, ValueError)):
            await kernel.create_process(
                "child2", parent_id=parent.id, capabilities=["file_read"], token_budget=8_000
            )
        assert c1.token_budget == 8_000

    _run(go())


def test_charge_tokens_does_not_overshoot_budget(wf) -> None:
    """P1-9：单次 charge 不得超过预算硬顶。"""

    async def go():
        kernel = wf["kernel"]
        proc = await kernel.create_process("bud", capabilities=["file_read"], token_budget=100)
        kernel.charge_tokens(proc.id, 80)
        with pytest.raises(BudgetExceededError):
            kernel.charge_tokens(proc.id, 50)
        p = kernel.get_process(proc.id)
        assert p is not None
        assert p.tokens_used == 80  # 未吃进超支部分

    _run(go())


def test_identity_list_filters_by_user(wf) -> None:
    """P0-1：registry.list 支持 user_id 过滤。"""

    async def go():
        reg = wf["registry"]
        u1, u2 = uuid.uuid4(), uuid.uuid4()
        await reg.create("u1-agent", user_id=u1)
        await reg.create("u2-agent", user_id=u2)
        only1 = await reg.list(user_id=u1)
        assert len(only1) == 1 and only1[0].name == "u1-agent"

    _run(go())


def test_memory_rollback_clears_current_without_self_loop_hack(wf) -> None:
    """P1-8：回滚后 current_memory 为空，且 superseded_by 不自指。"""

    async def go():
        reg, inbox, eng = wf["registry"], wf["inbox"], wf["engine"]
        ident = await reg.create("沉淀员", capabilities=["file_read"])
        for i in range(6):
            item = await inbox.enqueue(ident.id, f"任务{i}")
            await inbox.complete(item.id, "ok")
        props = await eng.analyze(ident.id)
        distill = [p for p in props if p.kind == "memory_distill"][0]
        await eng.approve(distill.id, by="boss")
        mem = await reg.current_memory(ident.id, kind="methodology")
        assert len(mem) == 1
        entry_id = mem[0].id
        await eng.rollback(distill.id, by="boss")
        assert await reg.current_memory(ident.id, kind="methodology") == []
        # 查原条目不应 self-supersede
        from sqlalchemy import select
        from backend.models.agent_identity import IdentityMemoryEntry

        async with reg._session_factory() as s:
            row = (
                await s.execute(select(IdentityMemoryEntry).where(IdentityMemoryEntry.id == entry_id))
            ).scalar_one()
            assert row.superseded_by is not None
            assert row.superseded_by != row.id

    _run(go())
