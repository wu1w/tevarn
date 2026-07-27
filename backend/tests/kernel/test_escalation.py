"""提权交互测试（0.4.1）：用户授权是唯一合法的能力扩大通道。"""

from __future__ import annotations

import asyncio

import pytest

from backend.kernel import AgentKernel, KernelPermissionError


def _run(coro):
    return asyncio.run(coro)


def test_escalation_full_lifecycle() -> None:
    """申请→pending→批准→能力并入→mediate 放行，三段事件全部入链。"""
    async def go():
        k = AgentKernel()
        proc = await k.create_process("main", capabilities=["file_read"])
        with pytest.raises(KernelPermissionError):
            await k.mediate(proc.id, "tool_call", "terminal")

        req = await k.request_escalation(proc.id, ["terminal"], reason="需要执行命令")
        assert req.status == "pending" and req.capabilities == ("terminal",)

        approved = await k.approve_escalation(req.id, by="user-1")
        assert approved.status == "approved" and approved.resolved_by == "user-1"
        assert "terminal" in (k.get_process(proc.id).capabilities or [])
        d = await k.mediate(proc.id, "tool_call", "terminal")
        assert d.allowed

        kinds = [e.kind for e in k.events()]
        assert "escalation_requested" in kinds
        assert "escalation_approved" in kinds
        ok, _ = k.verify_event_chain()
        assert ok

    _run(go())


def test_escalation_deny_keeps_block() -> None:
    """拒绝后能力集不变，调用仍被拦截。"""
    async def go():
        k = AgentKernel()
        proc = await k.create_process("main", capabilities=["file_read"])
        req = await k.request_escalation(proc.id, ["browser"])
        denied = await k.deny_escalation(req.id, by="user-1")
        assert denied.status == "denied"
        assert "browser" not in (k.get_process(proc.id).capabilities or [])
        with pytest.raises(KernelPermissionError):
            await k.mediate(proc.id, "tool_call", "browser")
        assert "escalation_denied" in [e.kind for e in k.events()]

    _run(go())


def test_escalation_approve_resigns_token() -> None:
    """进程持令牌时批准 → 令牌重签含新能力（否则 mediate 以旧令牌为准仍拦截）。

    注意：申请能力必须用高危项（terminal）——低风险能力（如 grep）会被
    approval_rules 的 auto_low_risk 通道自动批准，走不到手动批准路径。
    """
    async def go():
        k = AgentKernel()
        proc = await k.create_process("main", capabilities=["file_read"])
        k.issue_token(proc.id, ["file_read"])
        req = await k.request_escalation(proc.id, ["terminal"])
        await k.approve_escalation(req.id)
        d = await k.mediate(proc.id, "tool_call", "terminal")
        assert d.allowed
        assert "terminal" in k.get_process(proc.id).token.capabilities

    _run(go())


def test_escalation_dedup_pending() -> None:
    """模型重试拦截不刷屏：同进程同能力复用同一 pending 申请。"""
    async def go():
        k = AgentKernel()
        proc = await k.create_process("main", capabilities=["file_read"])
        r1 = await k.request_escalation(proc.id, ["terminal"])
        r2 = await k.request_escalation(proc.id, ["terminal"])
        r3 = await k.request_escalation(proc.id, ["terminal", "browser"])  # 超集→新申请
        assert r1.id == r2.id
        assert r3.id != r1.id
        pendings = k.list_escalations(status="pending")
        assert len(pendings) == 2

    _run(go())


def test_escalation_guards() -> None:
    """边界：兼容模式/终态/重复处理/未知申请。"""
    async def go():
        k = AgentKernel()
        compat = await k.create_process("legacy")  # 兼容模式
        with pytest.raises(ValueError, match="无需提权"):
            await k.request_escalation(compat.id, ["x"])

        proc = await k.create_process("main", capabilities=["file_read"])
        req = await k.request_escalation(proc.id, ["terminal"])
        await k.approve_escalation(req.id)
        with pytest.raises(ValueError, match="已处理"):
            await k.approve_escalation(req.id)
        with pytest.raises(ValueError, match="未知提权申请"):
            await k.approve_escalation("nonexistent")

        await k.end_process(proc.id)
        with pytest.raises(ValueError, match="已终止"):
            await k.request_escalation(proc.id, ["browser"])

    _run(go())


def test_escalation_child_approval_does_not_leak_to_parent() -> None:
    """子进程获批的能力不回流父进程（narrowing 方向不变）。"""
    async def go():
        k = AgentKernel()
        parent = await k.create_process("main", capabilities=["file_read", "grep", "terminal"])
        child = await k.create_process("sub:w", parent_id=parent.id, capabilities=["file_read"])
        req = await k.request_escalation(child.id, ["terminal"])
        await k.approve_escalation(req.id)
        assert "terminal" in (k.get_process(child.id).capabilities or [])
        # 父集未变；且子获批后仍不能超过父集申请（terminal 本在父集内）
        with pytest.raises(KernelPermissionError):
            await k.mediate(parent.id, "tool_call", "browser")

    _run(go())
