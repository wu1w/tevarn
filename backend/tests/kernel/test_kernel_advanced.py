"""Kernel 进阶测试（W2/W3/阶段 2/阶段 3 覆盖）。

- TC-C3：过期 Token 在 mediate 被拒
- TC-T 系：mediate 强制（tool/skill 动作语义一致）
- TC-B2：预算耗尽中断语义
- Intent：声明 → 最小权限合成（白名单/高危/父令牌 narrow）
- 哈希链：链式哈希完整性 + 篡改检测
"""

from __future__ import annotations

import asyncio
import time

import pytest

from backend.kernel import (
    AgentKernel,
    BudgetExceededError,
    CapabilityToken,
    IntentDeclaration,
    KernelPermissionError,
    synthesize_capabilities,
    synthesize_token,
)


@pytest.fixture
def kernel() -> AgentKernel:
    return AgentKernel()


# ── TC-C3：过期 / 范围外 Token 在 mediate 被拒 ──

def test_c3_expired_token_rejected_at_mediate(kernel: AgentKernel) -> None:
    async def go():
        proc = await kernel.create_process("main", capabilities=["file_read"])
        kernel.issue_token(proc.id, expires_at=time.time() - 1)  # 已过期
        with pytest.raises(KernelPermissionError, match="过期"):
            await kernel.mediate(proc.id, "tool_call", "file_read")

    asyncio.run(go())


def test_c3_out_of_scope_token_rejected(kernel: AgentKernel) -> None:
    async def go():
        proc = await kernel.create_process("main", capabilities=["file_read", "grep"])
        kernel.issue_token(proc.id, ["file_read"])  # 窄化令牌
        d = await kernel.mediate(proc.id, "tool_call", "file_read")
        assert d.allowed
        with pytest.raises(KernelPermissionError, match="令牌范围"):
            await kernel.mediate(proc.id, "tool_call", "grep")  # 进程有但令牌没有 → 拒

    asyncio.run(go())


# ── TC-T 系：动作语义 ──

def test_t2_skill_exec_action_mediated(kernel: AgentKernel) -> None:
    async def go():
        proc = await kernel.create_process("main", capabilities=["summarize_doc"])
        d = await kernel.mediate(proc.id, "skill_exec", "summarize_doc")
        assert d.allowed
        with pytest.raises(KernelPermissionError):
            await kernel.mediate(proc.id, "skill_exec", "rm_rf_world")
        # 事件带 action 语义
        ev = list(kernel.events(kind="mediation"))
        assert {e.detail["action"] for e in ev} == {"skill_exec"}

    asyncio.run(go())


def test_t5_child_process_narrowed_token(kernel: AgentKernel) -> None:
    """并行 fan-out 场景：子进程获得收窄后的能力，无法调用父级未授予工具。"""
    async def go():
        parent = await kernel.create_process("main", capabilities=["file_read", "grep", "terminal"])
        child = await kernel.create_process(
            "draft-worker", parent_id=parent.id, capabilities=["file_read"]
        )
        await kernel.mediate(child.id, "tool_call", "file_read")  # ok
        with pytest.raises(KernelPermissionError):
            await kernel.mediate(child.id, "tool_call", "terminal")  # 父有但子未授予 → 拒

    asyncio.run(go())


# ── TC-B2：预算耗尽中断 ──

def test_b2_budget_exhaustion_semantics(kernel: AgentKernel, monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.core.config.settings.agent_budget_soft_renew_enabled", False, raising=False
    )
    async def go():
        proc = await kernel.create_process("main", token_budget=1000)
        kernel.charge_tokens(proc.id, 600)
        assert proc.budget_remaining == 400
        with pytest.raises(BudgetExceededError):
            kernel.charge_tokens(proc.id, 401)
        # 预算事件进审计链
        assert len(kernel.events(kind="budget_exceeded")) == 1
        # 耗尽后进程仍在（由 loop 决定中断），但 mediate 仍可用——
        # 中断语义由 llm_round 的 _should_stop 落地（集成层）
        assert not proc.is_terminal

    asyncio.run(go())


# ── Intent Declaration ──

def test_intent_parse_validation() -> None:
    with pytest.raises(ValueError, match="goal"):
        IntentDeclaration.from_dict({"capabilities": ["file_read"]})
    intent = IntentDeclaration.from_dict({
        "goal": "读源码总结架构",
        "capabilities": ["file_read", "grep"],
        "constraints": {"token_budget": 20000},
    })
    assert intent.goal and intent.capabilities == ("file_read", "grep")


def test_intent_synthesize_whitelist() -> None:
    intent = IntentDeclaration.from_dict({
        "goal": "读写混合任务",
        "capabilities": ["file_read", "terminal", "unknown_tool"],
    })
    granted, dropped = synthesize_capabilities(intent)
    assert granted == ["file_read"]  # 安全能力直接授予
    assert set(dropped) == {"terminal", "unknown_tool"}  # 高危未接受风险 + 未知 → 剔除


def test_intent_synthesize_risky_with_consent() -> None:
    intent = IntentDeclaration.from_dict({
        "goal": "改代码",
        "capabilities": ["file_read", "file_write"],
        "constraints": {"allow_risky": True},
    })
    granted, dropped = synthesize_capabilities(intent)
    assert set(granted) == {"file_read", "file_write"}
    assert dropped == []


def test_intent_default_grant_readonly() -> None:
    """未声明能力 → 默认授予只读探索集（安全默认）。"""
    intent = IntentDeclaration.from_dict({"goal": "随便看看"})
    granted, dropped = synthesize_capabilities(intent)
    assert "file_read" in granted and "terminal" not in granted


def test_intent_token_narrowed_by_parent() -> None:
    parent = CapabilityToken(capabilities=frozenset({"file_read", "grep"}))
    intent = IntentDeclaration.from_dict({
        "goal": "子任务",
        "capabilities": ["file_read", "web_search"],  # web_search 父令牌没有
    })
    token, dropped = synthesize_token(intent, parent_token=parent, process_id="p1")
    assert token.capabilities == frozenset({"file_read"})
    assert "web_search" in dropped
    assert token.parent_token_id == parent.id


def test_intent_token_ttl() -> None:
    intent = IntentDeclaration.from_dict({
        "goal": "临时任务",
        "capabilities": ["file_read"],
        "constraints": {"ttl_seconds": 60},
    })
    token, _ = synthesize_token(intent)
    assert token.expires_at is not None and not token.is_expired
    assert token.expires_at - time.time() <= 60.5


def test_apply_intent_to_process_production_path(kernel: AgentKernel) -> None:
    """生产路径 helper：apply_intent_to_process 挂载 token + capabilities。"""
    from backend.kernel.intent import apply_intent_to_process

    async def go():
        proc = await kernel.create_process(
            "worker",
            capabilities=["file_read", "grep", "terminal"],
        )
        tok, dropped = apply_intent_to_process(
            kernel,
            proc.id,
            {
                "goal": "只读调研",
                "capabilities": ["file_read", "terminal"],
                "constraints": {},
            },
        )
        assert "terminal" in dropped
        assert "file_read" in tok.capabilities
        fresh = kernel.get_process(proc.id)
        assert fresh is not None
        assert fresh.token is not None
        assert "file_read" in (fresh.capabilities or [])
        assert "terminal" not in (fresh.capabilities or [])
        assert (fresh.meta or {}).get("intent", {}).get("goal") == "只读调研"
        d = await kernel.mediate(proc.id, "tool_call", "file_read")
        assert d.allowed
        with pytest.raises(KernelPermissionError):
            await kernel.mediate(proc.id, "tool_call", "terminal")

    asyncio.run(go())


def test_intent_declare_synthesize_mediate_integration(kernel: AgentKernel) -> None:
    """P2.2：声明 → 合成令牌 → 挂载进程 → mediate 放行/拒绝闭环。

    注意：挂载 token 后 court 以令牌为准（见 permission_court.decide_capability）。
    """

    async def go():
        intent = IntentDeclaration.from_dict({
            "goal": "只读调研源码",
            "capabilities": ["file_read", "grep", "terminal"],
            "constraints": {"token_budget": 5000},
        })
        token, dropped = synthesize_token(intent, process_id="pending")
        assert "terminal" in dropped  # 高危未 allow_risky
        assert "file_read" in token.capabilities

        # 进程能力可宽一些；真正边界由 intent 合成的令牌收窄
        proc = await kernel.create_process(
            "researcher",
            capabilities=["file_read", "grep", "terminal", "file_write"],
        )
        token2, dropped2 = synthesize_token(intent, process_id=proc.id)
        proc.token = token2
        assert "terminal" in dropped2
        assert "terminal" not in token2.capabilities

        d = await kernel.mediate(proc.id, "tool_call", "file_read")
        assert d.allowed is True
        with pytest.raises(KernelPermissionError, match="令牌范围"):
            await kernel.mediate(proc.id, "tool_call", "terminal")

        # allow_risky 后重新合成 → terminal 进入令牌 → mediate 放行
        intent_risk = IntentDeclaration.from_dict({
            "goal": "需要终端排查",
            "capabilities": ["file_read", "terminal"],
            "constraints": {"allow_risky": True},
        })
        tok_risk, drop_risk = synthesize_token(intent_risk, process_id=proc.id)
        assert "terminal" in tok_risk.capabilities
        assert drop_risk == []
        proc.token = tok_risk
        d2 = await kernel.mediate(proc.id, "tool_call", "terminal")
        assert d2.allowed is True

        # 父令牌 further narrow：子意图不能越权
        parent = tok_risk
        child_intent = IntentDeclaration.from_dict({
            "goal": "子任务只读",
            "capabilities": ["file_read", "terminal"],
            "constraints": {"allow_risky": True},
        })
        child_tok, child_drop = synthesize_token(
            child_intent, parent_token=parent, process_id=proc.id
        )
        # parent 有 terminal+file_read，子声明两者 → 全保留
        assert "file_read" in child_tok.capabilities
        # 再用更窄父令牌
        narrow_parent = CapabilityToken(
            capabilities=frozenset({"file_read"}), process_id=proc.id
        )
        child2, drop2 = synthesize_token(
            child_intent, parent_token=narrow_parent, process_id=proc.id
        )
        assert "terminal" in drop2
        assert child2.capabilities == frozenset({"file_read"})
        proc.token = child2
        with pytest.raises(KernelPermissionError, match="令牌范围"):
            await kernel.mediate(proc.id, "tool_call", "terminal")

    asyncio.run(go())


# ── 哈希链审计（阶段 3）──

def test_hash_chain_integrity(kernel: AgentKernel) -> None:
    async def go():
        p = await kernel.create_process("main")
        await kernel.mediate(p.id, "tool_call", "file_read")
        await kernel.end_process(p.id, state="completed")

    asyncio.run(go())
    ok, idx = kernel.verify_event_chain()
    assert ok and idx == -1
    # 链式：每条事件的 prev_hash == 前一条 hash
    events = kernel.events()
    for i in range(1, len(events)):
        assert events[i].prev_hash == events[i - 1].hash


def test_hash_chain_tamper_detected(kernel: AgentKernel) -> None:
    async def go():
        p = await kernel.create_process("main")
        await kernel.mediate(p.id, "tool_call", "file_read")

    asyncio.run(go())
    # 篡改历史事件内容（绕过 frozen 直接改内部字段模拟攻击）
    victim = kernel._events[0]
    object.__setattr__(victim, "detail", {"tampered": True})
    ok, idx = kernel.verify_event_chain()
    assert not ok and idx == 0


def test_events_carry_hash_in_api_dict(kernel: AgentKernel) -> None:
    async def go():
        await kernel.create_process("main")

    asyncio.run(go())
    d = kernel.events()[0].to_dict()
    assert d["hash"] and len(d["hash"]) == 64
    assert "prev_hash" in d
