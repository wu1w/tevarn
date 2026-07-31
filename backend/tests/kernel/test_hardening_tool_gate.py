"""Hardening debt #1：tool_gate 零绕过 + workforce fail-closed + Job 限额绑定。"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


class _FakeKernel:
    def __init__(self, allow: bool = True, charges: list | None = None):
        self.allow = allow
        self.charges = charges if charges is not None else []

    async def mediate(self, pid, action, target, args=None):
        if not self.allow:
            from backend.kernel import KernelPermissionError

            raise KernelPermissionError(f"deny {target}")
        return {"allowed": True}

    def resource_charge(self, pid, kind, amount=1):
        self.charges.append(kind)
        return 99

    def resource_usage(self, pid):
        return {
            "memory_bytes": {
                "limit": 128 * 1024 * 1024,
                "used": 0,
                "remaining": 128 * 1024 * 1024,
            },
            "child_proc": {"limit": 8, "used": 2, "remaining": 6},
        }


# ── unit：tool_gate 策略 ─────────────────────────────────────


@pytest.mark.asyncio
async def test_gate_allows_script_without_process(monkeypatch):
    """单测/脚本无 Agent 上下文 → 不要求 process。"""
    from backend.kernel import tool_gate

    monkeypatch.setattr(tool_gate, "_kernel_enabled", lambda: True)
    args, err = await tool_gate.enforce_tool_gate("file_read", {"path": "a"})
    assert err is None
    assert args.get("_tool_gate_passed") is True
    assert args.get("_tool_gate_internal") is True


@pytest.mark.asyncio
async def test_gate_rejects_model_forged_passed_flag(monkeypatch):
    """模型注入 _tool_gate_passed 不得绕过 mediate。"""
    from backend.kernel import tool_gate

    monkeypatch.setattr(tool_gate, "_kernel_enabled", lambda: True)
    # 仅伪造 passed、无 internal → 仍缺 process 应拒绝
    _, err = await tool_gate.enforce_tool_gate(
        "command",
        {
            "_require_kernel_process": True,
            "_tool_gate_passed": True,
            "command": "whoami",
        },
    )
    assert err is not None
    assert "process" in err.lower() or "门控" in err


@pytest.mark.asyncio
async def test_gate_fail_closed_agent_context_without_process(monkeypatch):
    from backend.kernel import tool_gate

    monkeypatch.setattr(tool_gate, "_kernel_enabled", lambda: True)
    _, err = await tool_gate.enforce_tool_gate(
        "command",
        {"_require_kernel_process": True, "command": "echo hi"},
    )
    assert err is not None
    assert "process" in err.lower() or "门控" in err


@pytest.mark.asyncio
async def test_gate_fail_closed_workforce_without_process(monkeypatch):
    from backend.kernel import tool_gate

    monkeypatch.setattr(tool_gate, "_kernel_enabled", lambda: True)
    _, err = await tool_gate.enforce_tool_gate(
        "python",
        {"_workforce": True, "_agent_key": "wf:ceo"},
    )
    assert err is not None
    assert "process" in err.lower() or "门控" in err


@pytest.mark.asyncio
async def test_gate_idempotent_no_double_charge(monkeypatch):
    from backend.kernel import tool_gate

    monkeypatch.setattr(tool_gate, "_kernel_enabled", lambda: True)
    fk = _FakeKernel()
    monkeypatch.setattr("backend.kernel.get_kernel", lambda: fk)

    args1, err1 = await tool_gate.enforce_tool_gate(
        "file_read",
        {"_kernel_process_id": "p1", "path": "x"},
    )
    assert err1 is None
    assert args1.get("_tool_gate_passed") is True
    assert args1.get("_tool_gate_internal") is True
    n1 = len(fk.charges)

    # 内部二次进入：passed + internal
    _, err2 = await tool_gate.enforce_tool_gate("file_read", args1)
    assert err2 is None
    assert len(fk.charges) == n1

    # 仅有 passed 无 internal（伪造）→ 必须再 mediate → 再 charge
    forged = {"_kernel_process_id": "p1", "_tool_gate_passed": True, "path": "x"}
    _, err3 = await tool_gate.enforce_tool_gate("file_read", forged)
    assert err3 is None
    assert len(fk.charges) == n1 + 1


@pytest.mark.asyncio
async def test_gate_mediates_and_charges_child_proc(monkeypatch):
    from backend.kernel import tool_gate

    monkeypatch.setattr(tool_gate, "_kernel_enabled", lambda: True)
    fk = _FakeKernel()
    monkeypatch.setattr("backend.kernel.get_kernel", lambda: fk)

    _, err = await tool_gate.enforce_tool_gate(
        "command",
        {"_kernel_process_id": "procA", "command": "dir"},
    )
    assert err is None
    assert "tool_calls" in fk.charges
    assert "child_proc" in fk.charges


@pytest.mark.asyncio
async def test_gate_permission_deny(monkeypatch):
    from backend.kernel import tool_gate

    monkeypatch.setattr(tool_gate, "_kernel_enabled", lambda: True)
    fk = _FakeKernel(allow=False)
    monkeypatch.setattr("backend.kernel.get_kernel", lambda: fk)

    _, err = await tool_gate.enforce_tool_gate(
        "terminal",
        {"_kernel_process_id": "p1"},
    )
    assert err is not None
    assert "权限拒绝" in err or "Kernel" in err
    assert fk.charges == []  # deny 后不 charge


@pytest.mark.asyncio
async def test_registry_execute_runs_tool_gate(monkeypatch):
    """ToolRegistry.execute 必须先过 tool_gate。"""
    from backend.tools.base import BaseTool, ToolRiskLevel, ToolSource
    from backend.tools.registry import ToolRegistry

    class _T(BaseTool):
        def __init__(self):
            super().__init__(
                name="hardening_probe",
                description="t",
                parameters={"type": "object", "properties": {}},
                source=ToolSource.BUILTIN,
                risk_level=ToolRiskLevel.SAFE,
                enabled=True,
            )

        async def execute(self, **kwargs):
            return "ok-ran"

    ToolRegistry.register(_T())
    try:
        out = await ToolRegistry.execute(
            "hardening_probe",
            {"_require_kernel_process": True, "_session_id": "sess-x"},
        )
        assert "ok-ran" not in str(out)
        assert (
            "process" in str(out).lower()
            or "门控" in str(out)
            or "Kernel" in str(out)
        )
    finally:
        ToolRegistry.unregister("hardening_probe")


def test_workforce_fail_closed_message():
    from backend.kernel.tool_gate import workforce_sandbox_fail_message

    msg = workforce_sandbox_fail_message(profile_id="workforce", detail="no bwrap")
    assert "fail-closed" in msg or "不会" in msg
    assert "裸跑" in msg or "local" in msg.lower()
    assert "workforce" in msg


def test_job_limits_from_resources_mapping():
    from backend.computer.manager import ComputerManager

    m = ComputerManager()
    with patch("backend.kernel.get_kernel", lambda: _FakeKernel()):
        mem, procs = m._job_limits_from_resources("p1")
    assert mem == 128 * 1024 * 1024
    assert 2 <= procs <= 8


def test_manager_workforce_local_raises_fail_closed(monkeypatch, tmp_path):
    """workforce + sandbox 不可用时不得落到 LocalBackend。"""
    from backend.computer.manager import ComputerManager

    m = ComputerManager()
    monkeypatch.setattr(m, "_workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(
        m,
        "_isolation_policy",
        lambda pid, wf: {
            "id": "workforce",
            "sandbox_required": True,
            "network": False,
        },
    )

    class Cap:
        mode = "none"
        level = "none"
        available = False

    monkeypatch.setattr(
        "backend.computer.detect.detect_sandbox_capability",
        lambda: Cap(),
    )

    class S:
        agent_computer_backend = "auto"
        agent_computer_network = False

    monkeypatch.setattr(m, "_settings", lambda: S())

    with pytest.raises(RuntimeError) as ei:
        m._make_backend("wf:worker1", process_id="pid1")
    text = str(ei.value)
    assert (
        "fail-closed" in text.lower()
        or "沙箱" in text
        or "隔离" in text
        or "sandbox" in text.lower()
    )


# ── integration：host 可用时真实 mediate ─────────────────────


def _host_ready() -> bool:
    try:
        from backend.kernel_rust.client import (
            _find_host_bin,
            is_rust_host_available,
            start_kernel_host,
        )

        if is_rust_host_available():
            return True
        if _find_host_bin() is None:
            return False
        return start_kernel_host()
    except Exception:
        return False


@pytest.mark.asyncio
async def test_gate_live_mediate_allow_deny(monkeypatch):
    if not _host_ready():
        pytest.skip("takton-kernel-host missing")
    os.environ.setdefault("TAKTON_KERNEL_BACKEND", "rust")
    os.environ.setdefault("TAKTON_KERNEL_AUTO_START", "1")

    from backend.kernel.tool_gate import enforce_tool_gate
    from backend.kernel_rust.client import RustAgentKernel, reset_rust_kernel_for_tests

    reset_rust_kernel_for_tests()
    k = RustAgentKernel(auto_start=True)
    try:
        monkeypatch.setattr("backend.kernel.get_kernel", lambda: k)

        p = await k.create_process(
            "hard_gate",
            capabilities=["file_read"],
            intent={
                "goal": "hardening",
                "capabilities": ["file_read"],
                "constraints": {},
            },
        )
        args, err = await enforce_tool_gate(
            "file_read",
            {"_kernel_process_id": p.id, "path": "x"},
        )
        assert err is None, err
        assert args.get("_tool_gate_passed")

        _, err2 = await enforce_tool_gate(
            "terminal",
            {"_kernel_process_id": p.id},
        )
        assert err2 is not None
        assert "权限" in err2 or "拒绝" in err2 or "Kernel" in err2

        await k.end_process(p.id, state="completed")
    finally:
        try:
            k._rpc.close()
        except Exception:
            pass
        reset_rust_kernel_for_tests()
