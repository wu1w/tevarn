"""0.7 受控进化测试（PLAN §3.d 红线验证）。

红线验收：
- 分析只产 pending，永不自动应用（无 auto_apply 路径）
- 审批→应用→回滚全链路事件入哈希链
- memory_distill 应用走 distilled+approved_by（数据结构级强制）
- 回滚恢复 before 状态
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.kernel import AgentKernel
from backend.kernel.audit_store import AuditEventStore
from backend.kernel.evolution_engine import EvolutionEngine
from backend.kernel.identity import IdentityRegistry
from backend.kernel.inbox import InboxService
from backend.kernel.workforce import build_org_view


@pytest.fixture()
def wf(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/wf.db", future=True)
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
    inbox = InboxService(kernel, SessionLocal)
    evo = EvolutionEngine(kernel, registry, SessionLocal)
    yield {
        "kernel": kernel, "registry": registry, "inbox": inbox,
        "engine": evo, "SessionLocal": SessionLocal,
    }
    asyncio.run(engine.dispose())


def _run(coro):
    return asyncio.run(coro)


async def _make_done_items(inbox, identity_id, n: int, fail: int = 0) -> None:
    for i in range(n):
        item = await inbox.enqueue(identity_id, f"调研任务 {i + 1}", source="cron")
        await inbox.complete(item.id, f"任务 {i + 1} 产出", process_id=None)
    for i in range(fail):
        item = await inbox.enqueue(identity_id, f"失败任务 {i + 1}", source="cron")
        # 直接打到 failed（attempts 上限路径太长，测试用 DB 状态）
        from backend.models.agent_identity import AgentInboxItem
        from sqlalchemy import select

        async with inbox._session_factory() as s:
            row = (
                await s.execute(select(AgentInboxItem).where(AgentInboxItem.id == item.id))
            ).scalar_one()
            row.status = "failed"
            row.attempts = 3
            row.error = "模拟失败"
            await s.commit()


def test_memory_distill_full_lifecycle(wf) -> None:
    """SOP 沉淀全流程：分析→建议→批准→Identity Memory 生效→回滚失效。"""
    async def go():
        reg, inbox, eng, kernel = wf["registry"], wf["inbox"], wf["engine"], wf["kernel"]
        ident = await reg.create("研究员", capabilities=["file_read"])
        await _make_done_items(inbox, ident.id, 6)

        proposals = await eng.analyze(ident.id)
        distill = [p for p in proposals if p.kind == "memory_distill"]
        assert len(distill) == 1
        p = distill[0]
        assert p.status == "pending"  # 红线：只产建议，永不自动应用
        assert (await reg.current_memory(ident.id)) == []  # 应用前无记忆

        # 重复分析不刷屏（同 kind pending 去重）
        assert await eng.analyze(ident.id) == []

        approved = await eng.approve(p.id, by="boss")
        assert approved.status == "applied"
        memory = await reg.current_memory(ident.id, kind="methodology")
        assert len(memory) == 1
        assert memory[0].source == "distilled" and memory[0].approved_by == "boss"

        rolled = await eng.rollback(p.id, by="boss")
        assert rolled.status == "rolled_back"
        assert (await reg.current_memory(ident.id, kind="methodology")) == []

        kinds = [e.kind for e in kernel.events()]
        for k in ("evolution_proposed", "evolution_approved", "evolution_applied", "evolution_rolled_back"):
            assert k in kinds
        ok, _ = kernel.verify_event_chain()
        assert ok

    _run(go())


def test_caps_adjust_apply_and_rollback(wf) -> None:
    """escalation 反复获批 → 建议并入编制 → 批准生效 → 回滚恢复。"""
    async def go():
        reg, eng, kernel = wf["registry"], wf["engine"], wf["kernel"]
        ident = await reg.create("研究员", capabilities=["file_read"])
        # 事件必须带 identity_id（或 process.meta）——跨身份隔离红线
        proc = await kernel.create_process(
            "wf:研究员",
            capabilities=["file_read"],
            meta={"identity_id": str(ident.id)},
        )
        for _ in range(2):
            kernel._emit("escalation_approved", proc.id, {
                "escalation_id": "x", "capabilities": ["browser"], "resolved_by": "boss",
                "identity_id": str(ident.id),
            })

        proposals = await eng.analyze(ident.id)
        adj = [p for p in proposals if p.kind == "caps_adjust"]
        assert len(adj) == 1
        await eng.approve(adj[0].id, by="boss")
        assert "browser" in (await reg.get(ident.id)).capabilities

        await eng.rollback(adj[0].id, by="boss")
        assert "browser" not in (await reg.get(ident.id)).capabilities

    _run(go())


def test_tool_deprecate(wf) -> None:
    """mediation 拒绝率 ≥50% 且样本 ≥5 → 建议淘汰 → 批准移出编制。"""
    async def go():
        reg, eng, kernel = wf["registry"], wf["engine"], wf["kernel"]
        ident = await reg.create("研究员", capabilities=["file_read", "legacy_tool"])
        proc = await kernel.create_process(
            "wf:研究员",
            capabilities=["file_read"],
            meta={"identity_id": str(ident.id)},
        )
        for i in range(6):
            kernel._emit("mediation", proc.id, {
                "action": "tool_call", "target": "legacy_tool",
                "allowed": i % 2 == 0,  # 50% 拒绝率
                "identity_id": str(ident.id),
            })
        proposals = await eng.analyze(ident.id)
        dep = [p for p in proposals if p.kind == "tool_deprecate"]
        assert len(dep) == 1
        await eng.approve(dep[0].id, by="boss")
        assert "legacy_tool" not in (await reg.get(ident.id)).capabilities
        await eng.rollback(dep[0].id, by="boss")
        assert "legacy_tool" in (await reg.get(ident.id)).capabilities

    _run(go())


def test_no_auto_apply_backdoor(wf) -> None:
    """红线：状态机守卫——非 pending 不能批准，非 applied 不能回滚。"""
    async def go():
        reg, inbox, eng = wf["registry"], wf["inbox"], wf["engine"]
        ident = await reg.create("研究员", capabilities=["file_read"])
        await _make_done_items(inbox, ident.id, 6)
        proposals = await eng.analyze(ident.id)
        p = [x for x in proposals if x.kind == "memory_distill"][0]

        with pytest.raises(ValueError, match="仅 applied 可回滚"):
            await eng.rollback(p.id, by="boss")  # pending 不能回滚
        await eng.reject(p.id, by="boss")
        with pytest.raises(ValueError, match="仅 pending 可批准"):
            await eng.approve(p.id, by="boss")  # rejected 不能批准
        assert (await reg.current_memory(ident.id)) == []  # 拒绝后无应用

    _run(go())


def test_planner_tune_on_high_failure(wf) -> None:
    """失败率超阈值 → planner 检讨建议 → 批准写 meta.planner_prefs。"""
    async def go():
        reg, inbox, eng = wf["registry"], wf["inbox"], wf["engine"]
        ident = await reg.create("倒霉蛋", capabilities=["file_read"])
        await _make_done_items(inbox, ident.id, 3, fail=3)  # 50% 失败率

        proposals = await eng.analyze(ident.id)
        tune = [p for p in proposals if p.kind == "planner_tune"]
        assert len(tune) == 1
        await eng.approve(tune[0].id, by="boss")
        assert (await reg.get(ident.id)).meta.get("planner_prefs", {}).get("verify_steps") is True
        await eng.rollback(tune[0].id, by="boss")
        assert "planner_prefs" not in ((await reg.get(ident.id)).meta or {})

    _run(go())


def test_org_view_reports_to_aggregation(wf) -> None:
    """汇报线观察：parent 链聚合为员工名（过滤 main/sub 噪音）。"""
    async def go():
        from backend.models.agent_identity import KernelProcessRecord
        import uuid as _u

        reg = wf["registry"]
        boss = await reg.create("老板", role="CEO", capabilities=["file_rw"])
        worker = await reg.create("研究员", role="research", capabilities=["file_rw"])

        SessionLocal = wf["SessionLocal"]
        async with SessionLocal() as s:
            s.add(KernelProcessRecord(
                process_id="p-boss",
                identity_key=f"wf:{boss.id}",
                capabilities=None,
                token_budget=None,
                tokens_used=100,
                state="completed",
            ))
            s.add(KernelProcessRecord(
                process_id="p-w1",
                identity_key=f"wf:{worker.id}",
                parent_process_id="p-boss",
                capabilities=["file_read"],
                token_budget=500,
                tokens_used=200,
                state="completed",
            ))
            s.add(KernelProcessRecord(
                process_id="p-w2",
                identity_key=f"wf:{worker.id}",
                parent_process_id="p-boss",
                capabilities=["file_read"],
                token_budget=500,
                tokens_used=300,
                state="failed",
            ))
            # 噪音：main→sub 不得进入 reports_to
            s.add(KernelProcessRecord(
                process_id="p-noise",
                identity_key="sub:deadbeef",
                parent_process_id="p-main-noise",
                capabilities=None,
                tokens_used=1,
                state="completed",
            ))
            s.add(KernelProcessRecord(
                process_id="p-main-noise",
                identity_key="main",
                capabilities=None,
                tokens_used=1,
                state="completed",
            ))
            await s.commit()

        view = await build_org_view(SessionLocal)
        assert view["total_processes"] >= 3
        rel = view["reports_to"]
        assert any(
            r["manager"] == "老板" and r["worker"] == "研究员" and r["delegations"] == 2
            for r in rel
        )
        # 不得泄漏内部 key
        for r in rel:
            assert not str(r["manager"]).startswith(("sub:", "wf:", "main"))
            assert not str(r["worker"]).startswith(("sub:", "wf:", "main"))

    _run(go())
