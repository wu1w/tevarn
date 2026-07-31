"""P0-C: schedule_run · llm admission · resource_charge (Rust host)."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

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
    return start_kernel_host()


@pytest.fixture(scope="module")
def k():
    if not _host_ready():
        pytest.skip("takton-kernel-host missing")
    from backend.kernel_rust.client import RustAgentKernel, reset_rust_kernel_for_tests

    reset_rust_kernel_for_tests()
    kernel = RustAgentKernel(auto_start=True)
    yield kernel
    try:
        kernel._rpc.close()
    except Exception:
        pass
    reset_rust_kernel_for_tests()


@pytest.mark.asyncio
async def test_schedule_run_and_stats(k) -> None:
    p = await k.create_process(
        "p0c_sched",
        capabilities=["file_read"],
        intent={"goal": "t", "capabilities": ["file_read"], "constraints": {}},
    )
    task = k.schedule_run(p.id, priority_class="foreground", payload={"x": 1})
    assert task.get("id")
    stats = k._call("scheduler_stats") or {}
    assert int(stats.get("queued") or 0) + int(stats.get("running") or 0) >= 1
    await k.end_process(p.id, state="completed")


@pytest.mark.asyncio
async def test_resource_charge_tool_calls(k) -> None:
    p = await k.create_process(
        "p0c_res",
        capabilities=["file_read"],
        intent={"goal": "t", "capabilities": ["file_read"], "constraints": {}},
    )
    rem = k.resource_charge(p.id, "tool_calls", 1)
    # unlimited by default → remaining is large or max
    usage = k.resource_usage(p.id)
    assert usage.get("tool_calls", {}).get("used") == 1
    k.resource_charge(p.id, "child_proc", 1)
    usage2 = k.resource_usage(p.id)
    assert usage2.get("child_proc", {}).get("used") == 1
    await k.end_process(p.id, state="completed")


@pytest.mark.asyncio
async def test_run_acquire_release(k) -> None:
    p = await k.create_process(
        "p0c_run",
        capabilities=["file_read"],
        intent={"goal": "t", "capabilities": ["file_read"], "constraints": {}},
    )
    rem = k.run_acquire(p.id)
    assert rem is not None
    usage = k.resource_usage(p.id)
    assert usage.get("concurrency_slots", {}).get("used") == 1
    assert k.run_release(p.id)
    usage2 = k.resource_usage(p.id)
    assert usage2.get("concurrency_slots", {}).get("used") == 0
    await k.end_process(p.id, state="completed")


@pytest.mark.asyncio
async def test_run_gate_queue_and_wake(k) -> None:
    """Global RunGate: max=1 → second run queues → release wakes by priority."""
    k.run_gate_set_max(1)
    try:
        p1 = await k.create_process(
            "p0c_gate1",
            capabilities=["file_read"],
            intent={"goal": "t", "capabilities": ["file_read"], "constraints": {}},
        )
        p2 = await k.create_process(
            "p0c_gate2",
            capabilities=["file_read"],
            intent={"goal": "t", "capabilities": ["file_read"], "constraints": {}},
        )
        g1 = k.run_gate_try(p1.id, priority_class="background")
        assert g1.get("status") == "granted", g1
        g2 = k.run_gate_try(p2.id, priority_class="foreground")
        assert g2.get("status") == "queued", g2
        rid = g2.get("request_id")
        assert rid
        st = k.run_gate_status()
        assert int(st.get("counts", {}).get("in_flight") or 0) == 1
        assert int(st.get("counts", {}).get("queued") or 0) >= 1
        assert k.run_gate_release(p1.id)
        polled = k.run_gate_poll(str(rid))
        assert polled.get("status") == "granted", polled
        assert k.run_gate_release(p2.id)
        await k.end_process(p1.id, state="completed")
        await k.end_process(p2.id, state="completed")
    finally:
        k.run_gate_set_max(4)


@pytest.mark.asyncio
async def test_llm_admission_rust_path(k) -> None:
    from backend.kernel.llm_admission import reset_llm_admission_for_tests, get_llm_admission
    from backend.kernel.llm_priority import LlmLeaseRequest, Priority
    from backend.kernel.kernel import get_kernel, reset_kernel_for_tests

    # Ensure get_kernel() returns rust client pointing at same host
    os.environ["TAKTON_KERNEL_BACKEND"] = "rust"
    reset_kernel_for_tests()
    reset_llm_admission_for_tests()
    # force singleton to our k by using k for acquire via direct RPC
    k._call(
        "llm_set_config",
        {
            "max_in_flight": 1,
            "owner_reserve": 0,
            "max_per_identity": 4,
            "queue_max": 8,
            "daily_global": 0,
            "daily_identity": 0,
        },
    )
    r1 = k._call(
        "llm_try_acquire",
        {"source": "chat", "priority": 100, "request_id": "t1"},
    )
    assert r1.get("status") == "granted"
    r2 = k._call(
        "llm_try_acquire",
        {"source": "workforce", "priority": 30, "request_id": "t2"},
    )
    assert r2.get("status") == "queued"
    k._call("llm_release", {"request_id": "t1"})
    polled = k._call("llm_poll", {"request_id": "t2"})
    assert polled.get("status") == "granted"
    k._call("llm_release", {"request_id": "t2"})
    st = k.llm_status()
    assert st.get("backend") == "rust"
    assert "in_flight" in st
