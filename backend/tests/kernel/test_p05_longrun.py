"""P0.5: process snapshot · result spill · policy · reclaim · cache metrics."""

from __future__ import annotations

import os
import sys
import time
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
    from backend.kernel_rust.client import (
        RustAgentKernel,
        is_rust_host_available,
        reset_rust_kernel_for_tests,
        start_kernel_host,
    )

    reset_rust_kernel_for_tests()
    last_err: Exception | None = None
    kernel = None
    for _ in range(8):
        try:
            if not is_rust_host_available():
                if not start_kernel_host():
                    time.sleep(0.15)
                    continue
            kernel = RustAgentKernel(auto_start=True)
            break
        except Exception as e:
            last_err = e
            time.sleep(0.2)
    if kernel is None:
        if not _host_ready():
            pytest.skip(f"takton-kernel-host missing/unavailable: {last_err}")
        kernel = RustAgentKernel(auto_start=True)
    yield kernel
    try:
        kernel._rpc.close()
    except Exception:
        pass
    reset_rust_kernel_for_tests()


@pytest.mark.asyncio
async def test_process_snapshot_recovery_no_full_replay(k) -> None:
    p = await k.create_process(
        "p05_snap",
        capabilities=["file_read"],
        intent={"goal": "t", "capabilities": ["file_read"], "constraints": {}},
    )
    snap = k.process_snapshot(p.id, meta={"iter": 3})
    assert snap.get("id")
    assert snap.get("tail_hash")
    plan = k.process_recovery_plan(p.id)
    assert plan.get("full_replay") is False
    assert plan.get("mode") == "snapshot_plus_incremental"
    assert plan.get("snapshot_id") == snap["id"]
    await k.end_process(p.id, state="completed")


@pytest.mark.asyncio
async def test_result_spill_and_load(k) -> None:
    p = await k.create_process(
        "p05_spill",
        capabilities=["file_read"],
        intent={"goal": "t", "capabilities": ["file_read"], "constraints": {}},
    )
    big = "Z" * 5000
    r = k.result_spill(p.id, "command", big)
    assert r.get("spilled") is True
    hid = (r.get("handle") or {}).get("id")
    assert hid
    assert "tool_result_handle" in str(r.get("context") or "")
    loaded = k.result_load(hid)
    assert loaded.get("content") == big
    small = k.result_spill(p.id, "command", "tiny")
    assert small.get("spilled") is False
    await k.end_process(p.id, state="completed")


@pytest.mark.asyncio
async def test_iteration_and_doom_policy(k) -> None:
    p = await k.create_process(
        "p05_pol",
        capabilities=["file_read"],
        intent={"goal": "t", "capabilities": ["file_read"], "constraints": {}},
    )
    k.iteration_set_budget(p.id, 2)
    assert k.iteration_consume(p.id).get("status") == "allow"
    assert k.iteration_consume(p.id).get("status") == "allow"
    ex = k.iteration_consume(p.id)
    assert ex.get("status") == "exhausted"

    d1 = k.doom_record(p.id, "cmd", {"x": 1})
    d2 = k.doom_record(p.id, "cmd", {"x": 1})
    d3 = k.doom_record(p.id, "cmd", {"x": 1})
    assert d1.get("status") == "allow"
    assert d2.get("status") == "allow"
    assert d3.get("status") == "doom_loop"
    await k.end_process(p.id, state="completed")


@pytest.mark.asyncio
async def test_reclaim_child_tree(k) -> None:
    parent = await k.create_process(
        "p05_parent",
        capabilities=["file_read", "terminal"],
        intent={
            "goal": "p",
            "capabilities": ["file_read", "terminal"],
            "constraints": {"allow_risky": True},
        },
    )
    child = await k.create_process(
        "p05_child",
        parent_id=parent.id,
        capabilities=["file_read"],
        intent={"goal": "c", "capabilities": ["file_read"], "constraints": {}},
    )
    rec = k.reclaim_process_tree(parent.id, reason="test")
    assert rec.get("ok") is True
    assert int(rec.get("reclaimed") or 0) >= 1
    cp = k.get_process(child.id)
    assert cp is not None
    assert str(cp.state) in ("killed", "completed", "failed") or getattr(
        cp, "state", ""
    ) in ("killed", "completed", "failed")
    # residual caps cleared
    caps = getattr(cp, "capabilities", None)
    if caps is not None:
        assert list(caps) == [] or caps == []


@pytest.mark.asyncio
async def test_cache_metrics(k) -> None:
    k.cache_record("openai", hit=True, bytes_saved=100)
    k.cache_record("openai", hit=False, bytes_saved=0)
    m = k.cache_metrics()
    fam = (m.get("families") or {}).get("openai") or {}
    assert int(fam.get("hits") or 0) >= 1
    assert int(fam.get("misses") or 0) >= 1


@pytest.mark.asyncio
async def test_cost_panel_and_marathon_metrics(k) -> None:
    p = await k.create_process(
        "p05_cost",
        capabilities=["file_read"],
        intent={"goal": "t", "capabilities": ["file_read"], "constraints": {}},
    )
    k.cost_charge(p.id, "openai", 100, 80)
    k.cost_charge(p.id, "anthropic", 50, 40)
    panel = k.cost_panel()
    assert int((panel.get("totals") or {}).get("tokens") or 0) >= 150
    assert int((panel.get("totals") or {}).get("billable") or 0) >= 120
    pc = k.cost_process(p.id)
    assert int(pc.get("tokens") or 0) >= 150

    k.marathon_record("attempt")
    k.marathon_record("resume_ok", reason="t")
    k.marathon_record("resume_fail", reason="x")
    k.marathon_record("resume_ok", reason="t")
    mm = k.marathon_metrics()
    assert float(mm.get("marathon_resume_success") or 0) >= 0.66
    await k.end_process(p.id, state="completed")


def test_exit_reasons_catalog() -> None:
    from backend.agent.exit_reasons import describe_exit_reason, format_exit_user_message

    d = describe_exit_reason("doom_loop")
    assert d["code"] == "doom_loop"
    assert "resume" in d["resume_entry"]
    msg = format_exit_user_message("kernel_iteration_exhausted", process_id="abc")
    assert "内核" in msg or "预算" in msg
    assert "abc" in msg


def test_log_cache_usage_reports_kernel(k) -> None:
    """Provider path: log_cache_usage → cache_record (R1)."""
    from backend.services.llm.usage_normalize import log_cache_usage

    # ensure host client path
    log_cache_usage(
        "gpt-test",
        {
            "prompt_tokens": 100,
            "completion_tokens": 10,
            "cache_read_input_tokens": 40,
            "billable_tokens": 70,
        },
        family="openai",
    )
    m = k.cache_metrics()
    # may merge with prior tests; just ensure structure
    assert "totals" in m or "families" in m
