"""0.6 自主运转测试：收件箱 + 派遣器（PLAN §3.f 红线验证）。

零 mock：真实 SQLite + 真 kernel + executor 注入（真实 async 执行函数，
内部走真实 kernel.mediate 验证权限——非伪造外部系统）。

红线验收：
- 唤醒路径全程过 mediate（越权工具被拦截）
- 预算扣减真实发生
- 收件箱有界（溢出丢弃最旧 + 审计）
- 编制内串行（同身份同时在手一单）
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.kernel import AgentKernel, KernelPermissionError
from backend.kernel.audit_store import AuditEventStore
from backend.kernel.dispatcher import WorkforceDispatcher
from backend.kernel.identity import IdentityRegistry
from backend.kernel.inbox import InboxService


@pytest.fixture()
def wf(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/wf.db", future=True)
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
    inbox = InboxService(kernel, SessionLocal, max_pending=3)
    yield {
        "kernel": kernel, "registry": registry, "inbox": inbox,
        "SessionLocal": SessionLocal, "store": store,
    }
    asyncio.run(engine.dispose())


def _run(coro):
    return asyncio.run(coro)


# ── 收件箱 ───────────────────────────────────────────────────


def test_enqueue_and_identity_gating(wf) -> None:
    async def go():
        reg, inbox = wf["registry"], wf["inbox"]
        ident = await reg.create("研究员", capabilities=["file_read"])
        item = await inbox.enqueue(ident.id, "调研今日新能源政策", source="cron")
        assert item is not None and item.status == "pending"

        await reg.suspend(ident.id, by="boss")
        rejected = await inbox.enqueue(ident.id, "再干一单", source="cron")
        assert rejected is None  # 停职拒收
        kinds = [e.kind for e in wf["kernel"].events()]
        assert "inbox_dropped" in kinds

        with pytest.raises(ValueError, match="未知身份"):
            import uuid as _u

            await inbox.enqueue(_u.uuid4(), "x")

    _run(go())


def test_inbox_bounded_overflow(wf) -> None:
    """有界红线：max_pending=3，第 4 单挤掉最旧 pending（dropped + 审计）。"""
    async def go():
        reg, inbox = wf["registry"], wf["inbox"]
        ident = await reg.create("研究员", capabilities=["file_read"])
        first = await inbox.enqueue(ident.id, "第 1 单")
        for i in range(2, 5):
            await inbox.enqueue(ident.id, f"第 {i} 单")
        pendings = await inbox.list_items(status="pending", limit=10)
        assert len(pendings) == 3
        dropped = await inbox.list_items(status="dropped", limit=10)
        assert len(dropped) == 1 and str(dropped[0].id) == str(first.id)
        assert "inbox_overflow_drop" in [e.kind for e in wf["kernel"].events()]

    _run(go())


def test_claim_priority_and_serial_per_identity(wf) -> None:
    """优先级降序；同身份在手一单（编制内串行）。"""
    async def go():
        reg, inbox = wf["registry"], wf["inbox"]
        a = await reg.create("员工A", capabilities=["file_read"])
        b = await reg.create("员工B", capabilities=["file_read"])
        await inbox.enqueue(a.id, "A 低优先级", priority=0)
        await inbox.enqueue(a.id, "A 高优先级", priority=9)
        await inbox.enqueue(b.id, "B 的单", priority=5)

        first = await inbox.claim_next()
        assert first.instruction == "A 高优先级"  # 优先级最高
        # A 在手 → 同身份跳过，领到 B
        second = await inbox.claim_next(busy_identity_ids={str(a.id)})
        assert second.instruction == "B 的单"

    _run(go())


# ── 派遣器 ───────────────────────────────────────────────────


def test_dispatcher_full_cycle_with_mediation(wf) -> None:
    """全流程：投递→派发→executor 内经 kernel.mediate 执行→结果回写→进程完结。

    executor 内部越权调用被拒绝（异步入口不绕过权限红线）。
    """
    async def go():
        reg, inbox, kernel = wf["registry"], wf["inbox"], wf["kernel"]
        ident = await reg.create("研究员", capabilities=["file_read"], default_token_budget=1000)
        item = await inbox.enqueue(ident.id, "读文件然后汇报")

        async def executor(identity, it, proc_id, k):
            # 编制内权限：放行
            await k.mediate(proc_id, "tool_call", "file_read")
            # 越权：被拦截（红线验证）
            with pytest.raises(KernelPermissionError):
                await k.mediate(proc_id, "tool_call", "terminal")
            # 预算真实扣减
            k.charge_tokens(proc_id, 100)
            return "报告：已读取 3 份文件"

        disp = WorkforceDispatcher(kernel, inbox, reg, wf["SessionLocal"], executor=executor)
        n = await disp.tick(wait=True)
        assert n == 1

        done = await inbox.list_items(status="done")
        assert len(done) == 1
        assert "已读取 3 份文件" in done[0].result
        assert done[0].process_id  # 工单关联了 kernel 进程
        proc = kernel.get_process(done[0].process_id)
        assert proc.state == "completed"
        assert proc.tokens_used == 100  # 预算真实扣减
        ok, _ = kernel.verify_event_chain()
        assert ok

    _run(go())


def test_dispatcher_retry_then_failed(wf) -> None:
    """executor 持续失败：attempts 到上限 → dead 死信（不无限重试）。"""
    async def go():
        reg, inbox, kernel = wf["registry"], wf["inbox"], wf["kernel"]
        ident = await reg.create("倒霉蛋", capabilities=["file_read"])
        item = await inbox.enqueue(ident.id, "必败任务")

        async def bad_executor(identity, it, proc_id, k):
            raise RuntimeError("爆炸了")

        disp = WorkforceDispatcher(kernel, inbox, reg, wf["SessionLocal"], executor=bad_executor)
        for _ in range(3):  # MAX_ATTEMPTS=3：每轮 claim→fail→放回
            await disp.tick(wait=True)
        dead = await inbox.list_items(status="dead")
        assert len(dead) == 1
        assert "爆炸了" in dead[0].error
        assert dead[0].attempts == 3

    _run(go())


def test_dispatcher_suspended_identity_not_dispatched(wf) -> None:
    """suspended 身份的工单挂起不派发（不丢，等复职）。"""
    async def go():
        reg, inbox, kernel = wf["registry"], wf["inbox"], wf["kernel"]
        ident = await reg.create("研究员", capabilities=["file_read"])
        item = await inbox.enqueue(ident.id, "等待复职的单")
        await reg.suspend(ident.id, by="boss")

        called = []

        async def executor(identity, it, proc_id, k):
            called.append(it.id)
            return "x"

        disp = WorkforceDispatcher(kernel, inbox, reg, wf["SessionLocal"], executor=executor)
        n = await disp.tick(wait=True)
        assert n == 0 and not called  # 停职不派发
        # 工单仍 pending
        pendings = await inbox.list_items(status="pending")
        assert len(pendings) == 1

        # 复职后可派发
        await reg.resume(ident.id, by="boss")
        n = await disp.tick(wait=True)
        assert n == 1 and len(called) == 1

    _run(go())


def test_daily_report_aggregation(wf) -> None:
    """日报：「你不在的这段时间」数据聚合。"""
    async def go():
        from backend.kernel.workforce import build_daily_report

        reg, inbox, kernel = wf["registry"], wf["inbox"], wf["kernel"]
        ident = await reg.create("研究员", capabilities=["file_read"])
        await inbox.enqueue(ident.id, "完成任务一", source="cron")

        async def executor(identity, it, proc_id, k):
            await k.mediate(proc_id, "tool_call", "file_read")
            return "任务一产出摘要"

        disp = WorkforceDispatcher(kernel, inbox, reg, wf["SessionLocal"], executor=executor)
        await disp.tick(wait=True)

        report = await build_daily_report(kernel, inbox, hours=24)
        assert report["inbox"]["stats"].get("done") == 1
        assert report["by_identity"][str(ident.id)]["done"] == 1
        assert "任务一产出摘要" in report["by_identity"][str(ident.id)]["latest_results"][0]
        assert report["kernel"]["event_kinds"].get("inbox_done", 0) >= 1

    _run(go())


def test_fallback_budget_applied_when_identity_has_none(wf) -> None:
    """异步兜底预算：身份未设默认预算 → 进程挂 fallback 硬顶（最后防线）。"""
    async def go():
        reg, inbox, kernel = wf["registry"], wf["inbox"], wf["kernel"]
        ident = await reg.create("无预算员工", capabilities=["file_read"])
        assert ident.default_token_budget is None
        item = await inbox.enqueue(ident.id, "研究型工单")

        async def executor(identity, it, proc_id, k):
            return "done"

        disp = WorkforceDispatcher(kernel, inbox, reg, wf["SessionLocal"], executor=executor)
        await disp.tick(wait=True)

        done = await inbox.list_items(status="done")
        proc = kernel.get_process(done[0].process_id)
        assert proc.token_budget == 100000  # fallback 生效（config 默认 agent_workforce_fallback_budget）
        assert proc.budget_remaining == 100000

        # 身份显式设预算：普通工单保持身份预算
        ident2 = await reg.create(
            "有预算员工", capabilities=["file_read"], default_token_budget=8000
        )
        await inbox.enqueue(ident2.id, "另一单说你好")
        await disp.tick(wait=True)
        dones = await inbox.list_items(status="done")
        proc2 = kernel.get_process(dones[0].process_id)
        assert proc2.token_budget == 8000  # 身份预算优先于 fallback

        # 审计类工单：自动抬预算（不改写 identity 默认值）
        ident3 = await reg.create(
            "审计员甲",
            role="安全审计员",
            capabilities=["file_read"],
            default_token_budget=30_000,
        )
        await inbox.enqueue(ident3.id, "请对 backend 做安全审计并 file_read 核实")
        await disp.tick(wait=True)
        dones3 = await inbox.list_items(status="done")
        # 取该身份最近完成
        item3 = next(i for i in dones3 if str(i.identity_id) == str(ident3.id))
        proc3 = kernel.get_process(item3.process_id)
        assert proc3.token_budget is not None and proc3.token_budget >= 120_000
        # identity 默认仍为 30k
        refreshed = await reg.get(ident3.id)
        assert refreshed.default_token_budget == 30_000

    _run(go())


# ── Alpha Review #2：WorkforceWorker 池 ────────────────────────


def test_worker_pool_reuses_loop_per_identity(wf) -> None:
    """同身份两单共享一个 loop 实例；不同身份各自独立。"""
    async def go():
        reg, inbox, kernel = wf["registry"], wf["inbox"], wf["kernel"]
        ident = await reg.create("池化员工", capabilities=["file_read"])

        disp = WorkforceDispatcher(kernel, inbox, reg, wf["SessionLocal"])
        loop1 = await disp._worker_for(ident)
        loop2 = await disp._worker_for(ident)
        assert loop1 is loop2  # 复用，不重建

        ident2 = await reg.create("另一员工", capabilities=["file_read"])
        loop3 = await disp._worker_for(ident2)
        assert loop3 is not loop1  # 不同身份不同 worker

        # evict 后重建
        disp.evict_worker(ident.id)
        loop4 = await disp._worker_for(ident)
        assert loop4 is not loop1

    _run(go())


def test_reset_run_state_clears_run_level_state(wf) -> None:
    """run 级状态归零：停止信号/搜索计数器/进程选项不得跨工单泄漏。"""
    async def go():
        reg, inbox, kernel = wf["registry"], wf["inbox"], wf["kernel"]
        ident = await reg.create("状态员工", capabilities=["file_read"])
        disp = WorkforceDispatcher(kernel, inbox, reg, wf["SessionLocal"])
        loop = await disp._worker_for(ident)

        # 模拟上一单跑完的脏状态
        loop._should_stop = True
        loop._search_fp_counter = {"abc": 3}
        loop._kernel_process_options = {"token_budget": 12345}
        loop._kernel_process = object()
        loop._run_recorder = object()
        loop._contract_wl_ready = True
        loop._contract_whitelist = {"file_read"}
        loop._llm_fail_streak = 7

        loop._reset_run_state()

        assert loop._should_stop is False
        assert loop._search_fp_counter == {}
        assert loop._kernel_process_options is None
        assert loop._kernel_process is None
        assert loop._run_recorder is None
        assert loop._contract_wl_ready is False
        assert loop._contract_whitelist is None
        assert loop._llm_fail_streak == 0

    _run(go())
