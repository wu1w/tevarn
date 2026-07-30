"""防双 Run + CEO 运行中追加预算。"""
from __future__ import annotations

import asyncio

import pytest

from backend.kernel import AgentKernel


@pytest.fixture
def kernel() -> AgentKernel:
    return AgentKernel()


def test_retire_live_identity_processes(kernel: AgentKernel) -> None:
    async def go():
        p1 = await kernel.create_process("wf:ident-a", token_budget=10_000)
        await kernel.mark_running(p1.id)
        p2 = await kernel.create_process("wf:ident-a", token_budget=10_000)
        await kernel.mark_running(p2.id)
        assert len(kernel.live_processes_for_identity("wf:ident-a")) == 2
        killed = await kernel.retire_live_identity_processes(
            "wf:ident-a", reason="test"
        )
        assert len(killed) == 2
        assert kernel.live_processes_for_identity("wf:ident-a") == []
        # 终态后幂等
        killed2 = await kernel.retire_live_identity_processes("wf:ident-a")
        assert killed2 == []

    asyncio.run(go())


def test_create_after_retire_is_single(kernel: AgentKernel) -> None:
    async def go():
        p1 = await kernel.create_process("wf:ident-b", token_budget=5_000)
        await kernel.mark_running(p1.id)
        await kernel.retire_live_identity_processes("wf:ident-b", reason="preflight")
        p2 = await kernel.create_process("wf:ident-b", token_budget=8_000)
        await kernel.mark_running(p2.id)
        live = kernel.live_processes_for_identity("wf:ident-b")
        assert len(live) == 1
        assert live[0].id == p2.id

    asyncio.run(go())


def test_top_up_budget_mid_run(kernel: AgentKernel) -> None:
    async def go():
        p = await kernel.create_process("wf:ident-c", token_budget=1000)
        await kernel.mark_running(p.id)
        kernel.charge_tokens(p.id, 800)
        r = kernel.top_up_budget(p.id, 2000, by="ceo:test", reason="long audit")
        assert r["ok"] is True
        assert r["token_budget"] == 3000
        assert r["tokens_used"] == 800
        assert r["budget_remaining"] == 2200
        # 追加后可继续扣
        rem = kernel.charge_tokens(p.id, 1500)
        assert rem == 700

    asyncio.run(go())


def test_top_up_rejects_terminal(kernel: AgentKernel) -> None:
    async def go():
        p = await kernel.create_process("wf:ident-d", token_budget=1000)
        await kernel.end_process(p.id, state="completed")
        with pytest.raises(ValueError, match="终态"):
            kernel.top_up_budget(p.id, 500)

    asyncio.run(go())


def test_top_up_rejects_non_positive(kernel: AgentKernel) -> None:
    async def go():
        p = await kernel.create_process("wf:ident-e", token_budget=1000)
        with pytest.raises(ValueError, match="正"):
            kernel.top_up_budget(p.id, 0)

    asyncio.run(go())


def test_soft_renew_on_charge_overflow(kernel: AgentKernel, monkeypatch) -> None:
    """charge 超预算时自动 soft_renew，任务不硬死。"""
    monkeypatch.setattr(
        "backend.core.config.settings.agent_budget_soft_renew_enabled",
        True,
        raising=False,
    )
    monkeypatch.setattr(
        "backend.core.config.settings.agent_budget_soft_renew_max",
        3,
        raising=False,
    )
    monkeypatch.setattr(
        "backend.core.config.settings.agent_budget_soft_renew_min_add",
        5_000,
        raising=False,
    )

    async def go():
        p = await kernel.create_process("wf:ident-soft", token_budget=1_000)
        await kernel.mark_running(p.id)
        kernel.charge_tokens(p.id, 900)
        # 下一刀会超 1000 → 应 soft_renew 后成功
        rem = kernel.charge_tokens(p.id, 500)
        fresh = kernel.get_process(p.id)
        assert fresh is not None
        assert fresh.token_budget is not None and fresh.token_budget > 1_000
        assert (fresh.meta or {}).get("soft_renew_count", 0) >= 1
        assert rem is not None and rem >= 0

    asyncio.run(go())


def test_soft_renew_respects_max(kernel: AgentKernel, monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.core.config.settings.agent_budget_soft_renew_enabled",
        True,
        raising=False,
    )
    monkeypatch.setattr(
        "backend.core.config.settings.agent_budget_soft_renew_max",
        1,
        raising=False,
    )
    monkeypatch.setattr(
        "backend.core.config.settings.agent_budget_soft_renew_min_add",
        100,
        raising=False,
    )
    from backend.kernel import BudgetExceededError

    async def go():
        p = await kernel.create_process("wf:ident-max", token_budget=200)
        await kernel.mark_running(p.id)
        # 第一次 overflow → renew 成功
        kernel.charge_tokens(p.id, 250)
        # 耗尽续航额度后再超 → 应失败
        fresh = kernel.get_process(p.id)
        assert fresh is not None
        # 强制把 soft_renew_count 顶满
        fresh.meta = dict(fresh.meta or {})
        fresh.meta["soft_renew_count"] = 99
        with pytest.raises(BudgetExceededError):
            kernel.charge_tokens(p.id, int(fresh.token_budget or 0) + 1)

    asyncio.run(go())


def test_hard_cap_allows_large_ceo_budget() -> None:
    from backend.agent.workforce_budget import clamp_ceo_budget, hard_cap

    assert hard_cap() >= 900_000
    assert clamp_ceo_budget(900_000) == 900_000
    assert clamp_ceo_budget(0) == 0


@pytest.mark.asyncio
async def test_claim_skips_identity_with_claimed_item():
    """同身份已有 claimed 工单时，pending 单不得再 claim。"""
    import uuid

    from backend.database import AsyncSessionLocal
    from backend.kernel.identity import IdentityRegistry
    from backend.kernel.inbox import InboxService

    k = AgentKernel()
    reg = IdentityRegistry(k, AsyncSessionLocal)
    inbox = InboxService(k, AsyncSessionLocal)
    name = f"dual_{uuid.uuid4().hex[:8]}"
    ident = await reg.create(
        name,
        role="tester",
        capabilities=["file_rw", "command"],
        default_token_budget=50_000,
    )
    i1 = await inbox.enqueue(
        identity_id=ident.id,
        instruction="job1 long",
        priority=10,
    )
    assert i1 is not None
    i2 = await inbox.enqueue(
        identity_id=ident.id,
        instruction="job2 should wait",
        priority=5,
    )
    assert i2 is not None
    claimed1 = await inbox.claim_next(busy_identity_ids=set())
    assert claimed1 is not None
    assert str(claimed1.id) == str(i1.id)
    # 第二单同身份不得领走
    claimed2 = await inbox.claim_next(busy_identity_ids=set())
    if claimed2 is not None:
        assert str(claimed2.identity_id) != str(ident.id)
    # 释放第一单
    await inbox.complete(i1.id, result="ok")
    claimed3 = await inbox.claim_next(busy_identity_ids=set())
    assert claimed3 is not None
    assert str(claimed3.id) == str(i2.id)
