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

    from backend.models.base import Base
    import backend.models  # noqa: F401

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
    """executor 持续失败：attempts 到上限 → failed（不无限重试）。"""
    async def go():
        reg, inbox, kernel = wf["registry"], wf["inbox"], wf["kernel"]
        ident = await reg.create("倒霉蛋", capabilities=["file_read"])
        item = await inbox.enqueue(ident.id, "必败任务")

        async def bad_executor(identity, it, proc_id, k):
            raise RuntimeError("爆炸了")

        disp = WorkforceDispatcher(kernel, inbox, reg, wf["SessionLocal"], executor=bad_executor)
        for _ in range(3):  # MAX_ATTEMPTS=3：每轮 claim→fail→放回
            await disp.tick(wait=True)
        failed = await inbox.list_items(status="failed")
        assert len(failed) == 1
        assert "爆炸了" in failed[0].error
        assert failed[0].attempts == 3

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
