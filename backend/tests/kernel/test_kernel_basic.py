"""Agent Kernel 基础行为测试（阶段 1/W1）。

覆盖计划测试矩阵：
- TC-K1 create_process 返回有效 AgentProcess，初始 state 正确
- TC-K2 多 Process id 唯一、互不干扰
- TC-K3 end_process 后状态/资源清理正确
- TC-C1 CapabilityToken 序列化/反序列化
- TC-C2 narrow 单调递减；扩能力抛 CapabilityEscalationError
- TC-B1/B3 预算扣减与子进程预算上限
"""

from __future__ import annotations

import asyncio

import pytest

from backend.kernel import (
    AgentKernel,
    BudgetExceededError,
    CapabilityEscalationError,
    CapabilityToken,
    KernelPermissionError,
)


@pytest.fixture
def kernel() -> AgentKernel:
    return AgentKernel()


def test_k1_create_process_valid(kernel: AgentKernel) -> None:
    proc = asyncio.run(kernel.create_process("main", session_id="s1"))
    assert proc.id and len(proc.id) == 16
    assert proc.state == "created"
    assert proc.identity == "main"
    assert proc.capabilities is None  # 兼容模式
    assert kernel.get_process(proc.id) is proc


def test_k2_multiple_processes_unique(kernel: AgentKernel) -> None:
    async def go() -> tuple:
        p1 = await kernel.create_process("main", session_id="s1")
        p2 = await kernel.create_process("coder", session_id="s2")
        return p1, p2

    p1, p2 = asyncio.run(go())
    assert p1.id != p2.id
    assert len(kernel.list_processes()) == 2
    asyncio.run(kernel.end_process(p1.id, state="completed"))
    alive = kernel.list_processes()
    assert len(alive) == 1 and alive[0].id == p2.id  # 互不干扰


def test_k3_end_process_terminal_and_gc(kernel: AgentKernel) -> None:
    async def go():
        proc = await kernel.create_process("main")
        await kernel.mark_running(proc.id)
        assert proc.state == "running" and proc.started_at is not None
        ended = await kernel.end_process(proc.id, state="completed", reason="done")
        return proc, ended

    proc, ended = asyncio.run(go())
    assert ended is not None and ended.state == "completed"
    assert ended.is_terminal and ended.ended_at is not None
    # 重复 end 幂等（不覆盖终态）
    again = asyncio.run(kernel.end_process(proc.id, state="failed"))
    assert again is not None and again.state == "completed"
    # gc 清理
    proc.ended_at = 0.0  # 模拟超时
    assert kernel.gc_terminal(older_than_seconds=1) == 1
    assert kernel.get_process(proc.id) is None


def test_k4_mediation_audit_events(kernel: AgentKernel) -> None:
    async def go():
        proc = await kernel.create_process("main")
        await kernel.mediate(proc.id, "tool_call", "file_read")
        return proc

    proc = asyncio.run(go())
    kinds = [e.kind for e in kernel.events()]
    assert "process_created" in kinds
    assert "mediation" in kinds
    assert "policy.decision" in kinds  # 0.5.2 权限一张网
    med = kernel.events(kind="mediation")[0]
    assert med.detail["allowed"] is True
    assert med.detail["capability_checked"] is False  # 兼容模式
    pol = kernel.events(kind="policy.decision")[0]
    assert pol.detail.get("outcome") == "allow"
    assert pol.detail.get("target") == "file_read"


def test_mediation_explicit_capability_enforced(kernel: AgentKernel) -> None:
    async def go():
        proc = await kernel.create_process("main", capabilities=["file_read", "grep"])
        d = await kernel.mediate(proc.id, "tool_call", "file_read")
        assert d.allowed and d.capability_checked
        with pytest.raises(KernelPermissionError):
            await kernel.mediate(proc.id, "tool_call", "terminal")
        # 事件记录了拒绝
        denied = [e for e in kernel.events(kind="mediation") if not e.detail["allowed"]]
        assert len(denied) == 1 and denied[0].detail["target"] == "terminal"

    asyncio.run(go())


def test_mediation_terminal_process_rejected(kernel: AgentKernel) -> None:
    async def go():
        proc = await kernel.create_process("main")
        await kernel.end_process(proc.id, state="completed")
        with pytest.raises(KernelPermissionError):
            await kernel.mediate(proc.id, "tool_call", "file_read")

    asyncio.run(go())


def test_c1_token_serialization_roundtrip() -> None:
    tok = CapabilityToken(capabilities=frozenset({"file_read", "grep"}), process_id="p1")
    data = tok.to_dict()
    restored = CapabilityToken.from_dict(data)
    assert restored.capabilities == tok.capabilities
    assert restored.id == tok.id and restored.process_id == "p1"
    assert restored.allows("file_read") and not restored.allows("terminal")


def test_c2_narrow_monotonic() -> None:
    parent = CapabilityToken(capabilities=frozenset({"file_read", "grep", "glob"}))
    child = parent.narrow(["file_read"])
    assert child.capabilities == frozenset({"file_read"})
    assert child.parent_token_id == parent.id
    with pytest.raises(CapabilityEscalationError):
        parent.narrow(["file_read", "terminal"])
    # 通配父 Token 允许任意子集
    wild = CapabilityToken(capabilities=frozenset({"*"}))
    assert wild.narrow(["file_read"]).allows("file_read")


def test_c2_narrow_expiry_monotonic() -> None:
    parent = CapabilityToken(capabilities=frozenset({"*"}), expires_at=1000.0)
    child = parent.narrow(["file_read"], expires_at=500.0)
    assert child.expires_at == 500.0
    later = parent.narrow(["file_read"], expires_at=2000.0)
    assert later.expires_at == 1000.0  # 不得晚于父 Token


def test_expired_token_denies() -> None:
    tok = CapabilityToken(capabilities=frozenset({"*"}), expires_at=1.0)  # 1970 年，必过期
    assert tok.is_expired and not tok.allows("file_read")


def test_b1_charge_tokens_and_exceeded(kernel: AgentKernel, monkeypatch) -> None:
    # soft_renew 会在撞墙前自动 top_up，本用例验证硬顶拒绝
    monkeypatch.setattr(
        "backend.core.config.settings.agent_budget_soft_renew_enabled", False, raising=False
    )
    async def go():
        proc = await kernel.create_process("main", token_budget=100)
        assert kernel.charge_tokens(proc.id, 40) == 60
        with pytest.raises(BudgetExceededError):
            kernel.charge_tokens(proc.id, 61)
        assert [e.kind for e in kernel.events(kind="budget_exceeded")] == ["budget_exceeded"]

    asyncio.run(go())


def test_b3_child_budget_capped_by_parent(kernel: AgentKernel) -> None:
    async def go():
        parent = await kernel.create_process("main", token_budget=100)
        kernel.charge_tokens(parent.id, 30)
        with pytest.raises(BudgetExceededError):
            await kernel.create_process("sub", parent_id=parent.id, token_budget=71)
        child = await kernel.create_process("sub", parent_id=parent.id, token_budget=70)
        assert child.token_budget == 70

    asyncio.run(go())


def test_child_capabilities_subset_of_parent(kernel: AgentKernel) -> None:
    async def go():
        parent = await kernel.create_process("main", capabilities=["file_read", "grep"])
        with pytest.raises(CapabilityEscalationError):
            await kernel.create_process("sub", parent_id=parent.id, capabilities=["terminal"])
        child = await kernel.create_process("sub", parent_id=parent.id, capabilities=["grep"])
        assert child.capabilities == ["grep"]
        # 未指定能力时继承父集
        heir = await kernel.create_process("sub2", parent_id=parent.id)
        assert heir.capabilities == ["file_read", "grep"]

    asyncio.run(go())


def test_create_child_of_terminal_parent_rejected(kernel: AgentKernel) -> None:
    async def go():
        parent = await kernel.create_process("main")
        await kernel.end_process(parent.id, state="completed")
        with pytest.raises(ValueError):
            await kernel.create_process("sub", parent_id=parent.id)

    asyncio.run(go())


def test_issue_token_scoped_to_process(kernel: AgentKernel) -> None:
    async def go():
        proc = await kernel.create_process("main", capabilities=["file_read", "grep"])
        tok = kernel.issue_token(proc.id)
        assert tok.capabilities == frozenset({"file_read", "grep"})
        with pytest.raises(CapabilityEscalationError):
            kernel.issue_token(proc.id, ["terminal"])

    asyncio.run(go())
