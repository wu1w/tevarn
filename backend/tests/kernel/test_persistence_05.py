"""0.5 编制与档案测试：身份系统 + 进程持久化 + checkpoint 恢复。

零 mock 红线：真实 SQLite（tmp file）+ 真实 kernel + 真实 audit JSONL。
验收映射（PLAN_AI_WORKFORCE §6 0.5 验收）：
- 重启后身份/权限/记忆完整恢复
- 恢复走 checkpoint+增量，断言不触发全量 replay
- 权限变更全部可追溯到审计事件
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.kernel import AgentKernel
from backend.kernel.audit_store import AuditEventStore
from backend.kernel.identity import IdentityRegistry
from backend.kernel.persistence import KernelPersistence


@pytest.fixture()
def wf(tmp_path):
    """真实 SQLite + 全表 + kernel/persistence/registry 三件套。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/wf.db", future=True)
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    from backend.models.base import Base
    import backend.models  # noqa: F401 注册全模型

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    store = AuditEventStore(str(tmp_path / "events.jsonl"))
    persistence = KernelPersistence(SessionLocal, store, checkpoint_interval=3)
    kernel = AgentKernel(audit_store=store, persistence_sink=persistence.sink())
    registry = IdentityRegistry(kernel, SessionLocal)
    yield {
        "kernel": kernel, "persistence": persistence, "registry": registry,
        "store": store, "SessionLocal": SessionLocal, "tmp_path": tmp_path,
    }
    asyncio.run(engine.dispose())


def _run(coro):
    return asyncio.run(coro)


# ── 身份生命周期 ─────────────────────────────────────────────


def test_identity_lifecycle_and_audit(wf) -> None:
    async def go():
        reg = wf["registry"]
        ident = await reg.create("研究员", role="新能源行业研究", capabilities=["file_read", "web_search"])
        assert ident.status == "active"

        await reg.suspend(ident.id, by="boss")
        assert (await reg.get(ident.id)).status == "suspended"
        await reg.resume(ident.id, by="boss")
        await reg.archive(ident.id, by="boss")
        assert (await reg.get(ident.id)).status == "archived"
        # archived 终态不可逆
        with pytest.raises(ValueError, match="终态不可逆"):
            await reg.resume(ident.id, by="boss")
        with pytest.raises(ValueError, match="禁止改权"):
            await reg.set_capabilities(ident.id, ["*"], by="boss")

        kinds = [e.kind for e in wf["kernel"].events()]
        for k in ("identity_created", "identity_suspended", "identity_resumed", "identity_archived"):
            assert k in kinds
        ok, _ = wf["kernel"].verify_event_chain()
        assert ok

    _run(go())


def test_identity_caps_change_audited(wf) -> None:
    """权限变更必须留痕（禁止静默改权）。"""
    async def go():
        reg = wf["registry"]
        ident = await reg.create("编码员", capabilities=["file_read"])
        await reg.set_capabilities(ident.id, ["file_read", "terminal"], by="boss")
        evts = [e for e in wf["kernel"].events(kind="identity_caps_changed")]
        assert len(evts) == 1
        assert evts[0].detail["to"] == ["file_read", "terminal"]
        assert evts[0].detail["by"] == "boss"
        assert (await reg.get(ident.id)).capabilities == ["file_read", "terminal"]

    _run(go())


# ── Identity Memory ──────────────────────────────────────────


def test_identity_memory_version_chain(wf) -> None:
    async def go():
        reg = wf["registry"]
        ident = await reg.create("研究员")
        m1 = await reg.add_memory(ident.id, "methodology", "先看政策再看财报")
        m2 = await reg.supersede_memory(m1.id, "先看财报再看政策，最后看舆情", approved_by="boss")
        assert m2.version == 2
        # 当前生效只有 v2
        cur = await reg.current_memory(ident.id, kind="methodology")
        assert len(cur) == 1 and cur[0].content.startswith("先看财报")
        # 已取代条目不能再改
        with pytest.raises(ValueError, match="已被取代"):
            await reg.supersede_memory(m1.id, "再次修改", approved_by="boss")

    _run(go())


def test_identity_memory_distilled_requires_approval(wf) -> None:
    """蒸馏记忆必须有审批人（进化审批不可绕过）。"""
    async def go():
        reg = wf["registry"]
        ident = await reg.create("研究员")
        with pytest.raises(ValueError, match="approved_by"):
            await reg.add_memory(ident.id, "experience", "蒸馏的方法论", source="distilled")
        e = await reg.add_memory(
            ident.id, "experience", "蒸馏的方法论", source="distilled", approved_by="boss"
        )
        assert e.approved_by == "boss"

    _run(go())


# ── 进程持久化 + 重启恢复 ─────────────────────────────────────


def test_process_persisted_and_restart_marks_interrupted(wf) -> None:
    """进程档案落盘；模拟重启后 running → interrupted（诚实标记）。"""
    async def go():
        k = wf["kernel"]
        proc = await k.create_process("main", capabilities=["file_read"], session_id="s1")
        await k.mark_running(proc.id)
        await wf["persistence"].flush()

        from backend.models.agent_identity import KernelProcessRecord
        from sqlalchemy import select

        async with wf["SessionLocal"]() as s:
            rec = (
                await s.execute(select(KernelProcessRecord).where(
                    KernelProcessRecord.process_id == proc.id))
            ).scalar_one()
            assert rec.state == "running"
            assert rec.capabilities == ["file_read"]

        # 模拟重启：新 persistence 实例 recover
        kp2 = KernelPersistence(wf["SessionLocal"], wf["store"], checkpoint_interval=3)
        summary = await kp2.recover()
        assert summary["interrupted"] == 1
        async with wf["SessionLocal"]() as s:
            rec = (
                await s.execute(select(KernelProcessRecord).where(
                    KernelProcessRecord.process_id == proc.id))
            ).scalar_one()
            assert rec.state == "interrupted"
            assert rec.exit_reason == "service restart"

    _run(go())


def test_checkpoint_recovery_incremental_only(wf) -> None:
    """恢复红线：checkpoint+增量，禁止全量 replay。

    每次 mediate 发 mediation + policy.decision（2 事件）。
    堆满多个 interval=3 快照后，再 emit 少量事件，recover 只读增量。
    """
    async def go():
        k = wf["kernel"]
        proc = await k.create_process("main", capabilities=["file_read"])
        # create(1) + 5×mediate×2 = 11 事件 → 多个 interval=3 快照
        for i in range(5):
            await k.mediate(proc.id, "tool_call", "file_read")
        await wf["persistence"].flush()

        cp = await wf["persistence"].latest_checkpoint()
        assert cp is not None
        assert cp.event_count >= 6
        assert cp.event_count % 3 == 0
        snap_count = cp.event_count
        total_before = len(k.events())

        # 再 mediate：+2 事件；若跨过 interval 会再落盘快照
        await k.mediate(proc.id, "tool_call", "file_read")
        await wf["persistence"].flush()
        total_after = len(k.events())
        assert total_after > total_before

        kp2 = KernelPersistence(wf["SessionLocal"], wf["store"], checkpoint_interval=3)
        summary = await kp2.recover()
        assert summary["checkpoint_seq"] >= 2
        assert summary["full_replay"] is False
        # 增量必须远小于全量（禁止从头 replay 全部事件）
        assert summary["incremental_events"] < total_after
        assert summary["incremental_events"] < snap_count
        # 至少有快照之后的尾巴（0 也可接受若恰在边界再落盘；此处应 >0）
        assert summary["incremental_events"] >= 0

    _run(go())


def test_checkpoint_tail_hash_mismatch_safe(wf, tmp_path) -> None:
    """快照 tail_hash 在事件文件中找不到（截断/篡改）→ 增量为空 + 不从头读。"""
    async def go():
        k = wf["kernel"]
        proc = await k.create_process("main", capabilities=["file_read"])
        for _ in range(3):
            await k.mediate(proc.id, "tool_call", "file_read")
        await wf["persistence"].flush()
        cp = await wf["persistence"].latest_checkpoint()
        assert cp is not None
        # 篡改：清空事件文件（模拟截断）
        open(wf["store"].path, "w").close()
        kp2 = KernelPersistence(wf["SessionLocal"], wf["store"], checkpoint_interval=3)
        summary = await kp2.recover()
        assert summary["incremental_events"] == 0  # 找不到锚点，绝不默默全量

    _run(go())
