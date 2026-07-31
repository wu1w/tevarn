"""ABI v1 golden tests against takton-kernel-host (Rust).

Requires host binary (auto-started). Skip if binary missing and host down.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Ensure repo root on path when run directly
_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault("TAKTON_KERNEL_BACKEND", "rust")
os.environ.setdefault("TAKTON_KERNEL_AUTO_START", "1")


def _host_ready() -> bool:
    from backend.kernel_rust.client import (
        _find_host_bin,
        is_rust_host_available,
        start_kernel_host,
    )

    if is_rust_host_available():
        return True
    if _find_host_bin() is None:
        return False
    return bool(start_kernel_host())


@pytest.fixture(scope="module")
def rust_kernel():
    from backend.kernel_rust.client import (
        RustAgentKernel,
        is_rust_host_available,
        reset_rust_kernel_for_tests,
        start_kernel_host,
    )

    if not _host_ready():
        pytest.skip(
            "takton-kernel-host binary not found; run: cargo build -p takton-kernel-host"
        )
    reset_rust_kernel_for_tests()
    # ensure host still up (previous tests may have disrupted listeners)
    if not is_rust_host_available():
        if not start_kernel_host():
            pytest.skip("kernel host failed to start")
    try:
        k = RustAgentKernel(auto_start=True)
    except (ConnectionError, OSError) as e:
        if not start_kernel_host():
            pytest.skip(f"kernel host connect failed: {e}")
        k = RustAgentKernel(auto_start=True)
    yield k
    try:
        k._rpc.close()
    except Exception:
        pass
    reset_rust_kernel_for_tests()


def test_abi_version(rust_kernel) -> None:
    r = rust_kernel._call("abi_version")
    assert r["abi"] == "1.0.0"
    assert r.get("kernel")
    assert r.get("runtime")


def test_list_methods_covers_core(rust_kernel) -> None:
    r = rust_kernel._call("list_methods")
    methods = set(r["methods"])
    for m in (
        "create_process",
        "mediate",
        "charge_tokens",
        "verify_event_chain",
        "get_escalation",
        "scheduler_complete",
        "live_processes_for_identity",
        "abi_version",
    ):
        assert m in methods, f"missing ABI method {m}"


@pytest.mark.asyncio
async def test_golden_create_mediate_charge_chain(rust_kernel) -> None:
    from backend.kernel_rust.client import KernelPermissionError

    p = await rust_kernel.create_process(
        "abi_py",
        session_id="s1",
        capabilities=["file_read", "grep"],
        token_budget=1000,
    )
    assert len(p.id) == 16
    assert p.state == "created"
    await rust_kernel.mark_running(p.id)

    d = await rust_kernel.mediate(p.id, "tool_call", "file_read")
    assert d.allowed and d.capability_checked

    with pytest.raises(KernelPermissionError):
        await rust_kernel.mediate(p.id, "tool_call", "terminal")

    rem = rust_kernel.charge_tokens(p.id, 100)
    assert rem == 900

    ok, idx = rust_kernel.verify_event_chain()
    assert ok, f"chain break at {idx}"

    kinds = [e.kind for e in rust_kernel.events()]
    assert "process_created" in kinds
    assert "mediation" in kinds
    assert "policy.decision" in kinds

    await rust_kernel.end_process(p.id, state="completed", reason="abi")


@pytest.mark.asyncio
async def test_escalation_get_and_scheduler(rust_kernel) -> None:
    p = await rust_kernel.create_process(
        "abi_esc",
        capabilities=["file_read"],
    )
    req = await rust_kernel.request_escalation(p.id, ["terminal"], reason="need")
    assert req.status == "pending"
    got = rust_kernel._call("get_escalation", {"request_id": req.id})
    assert got and got["id"] == req.id

    approved = await rust_kernel.approve_escalation(req.id, by="test")
    assert approved.status == "approved"
    fresh = rust_kernel.get_process(p.id)
    assert fresh and "terminal" in (fresh.capabilities or [])

    task = rust_kernel._call(
        "scheduler_submit",
        {"process_id": p.id, "payload": {"op": "x"}, "priority": 5},
    )
    tid = task["id"]
    nxt = rust_kernel._call("scheduler_next")
    assert nxt and nxt["id"] == tid
    rust_kernel._call("scheduler_complete", {"task_id": tid, "cancelled": False})
    stats = rust_kernel._call("scheduler_stats")
    assert int(stats.get("done") or 0) >= 1

    await rust_kernel.end_process(p.id, state="completed")


@pytest.mark.asyncio
async def test_live_identity_rpc(rust_kernel) -> None:
    a = await rust_kernel.create_process("wf:abi_id")
    b = await rust_kernel.create_process("wf:abi_id")
    r = rust_kernel._call("live_processes_for_identity", {"identity": "wf:abi_id"})
    assert r["total"] >= 2
    killed = await rust_kernel.retire_live_identity_processes(
        "wf:abi_id", reason="test", except_process_id=a.id
    )
    assert b.id in killed
    await rust_kernel.end_process(a.id, state="completed")
