#!/usr/bin/env python3
"""P0 end-to-end integration smoke against takton-kernel-host.

Covers A–D critical paths in one sequential scenario:
  host up → abi → create+intent → schema filter → mediate
  → schedule/run slots → llm admission → resource charge
  → decide_tool secret/ask → isolation → checkpoint → trail

Exit 0 on success. Run from repo root with host binary available.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TAKTON_KERNEL_BACKEND", "rust")
os.environ.setdefault("TAKTON_KERNEL_AUTO_START", "1")
os.environ.setdefault("TAKTON_KERNEL_REQUIRE_INTENT", "true")


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    raise SystemExit(1)


def ok(msg: str) -> None:
    print(f"  OK  {msg}")


def main() -> int:
    from backend.kernel_rust.client import (
        KernelPermissionError,
        RustAgentKernel,
        _find_host_bin,
        is_rust_host_available,
        reset_rust_kernel_for_tests,
        start_kernel_host,
    )

    print("=== P0 Integration Smoke ===")
    bin_path = _find_host_bin()
    print(f"host bin: {bin_path}")
    if not is_rust_host_available():
        if not start_kernel_host():
            fail("cannot start kernel host")
    ok("host listening")

    reset_rust_kernel_for_tests()
    if not is_rust_host_available():
        if not start_kernel_host():
            fail("cannot (re)start kernel host before client connect")
    k = RustAgentKernel(auto_start=True)

    # A: ABI
    ver = k.abi_version()
    assert ver.get("abi") == "1.0.0", ver
    methods = set(k.list_methods())
    required = {
        "create_process",
        "mediate",
        "apply_intent",
        "filter_tools",
        "schedule_run",
        "llm_try_acquire",
        "resource_charge",
        "decide_tool",
        "isolation_set_profile",
        "checkpoint_begin",
        "export_decision_trail",
    }
    missing = required - methods
    if missing:
        fail(f"ABI methods missing: {missing}")
    ok(f"abi {ver['abi']} methods={len(methods)}")

    # B: intent default readonly (no caps)
    p = __import__("asyncio").run(
        k.create_process("p0_smoke", session_id="smoke-s1")
    )
    if not p.capabilities or "file_read" not in p.capabilities:
        fail(f"expected readonly caps, got {p.capabilities}")
    if "terminal" in (p.capabilities or []):
        fail("terminal should not be in default caps")
    ok(f"create_process readonly caps={p.capabilities[:5]}…")

    # B: filter tools
    filtered = k.filter_tools(
        p.id, ["file_read", "grep", "terminal", "file_write"]
    )
    if "terminal" in filtered or "file_write" in filtered:
        fail(f"schema filter leaked risky tools: {filtered}")
    if "file_read" not in filtered:
        fail(f"file_read missing from filter: {filtered}")
    ok(f"filter_tools -> {filtered}")

    # mediate allow/deny
    __import__("asyncio").run(k.mark_running(p.id))
    d = __import__("asyncio").run(k.mediate(p.id, "tool_call", "file_read"))
    assert d.allowed
    try:
        __import__("asyncio").run(k.mediate(p.id, "tool_call", "terminal"))
        fail("terminal should be denied")
    except KernelPermissionError:
        ok("mediate deny terminal")

    # C: schedule + run slot + resource
    task = k.schedule_run(p.id, priority_class="foreground")
    assert task.get("id")
    rem = k.run_acquire(p.id)
    ok(f"schedule_run + run_acquire remaining={rem}")
    k.resource_charge(p.id, "tool_calls", 1)
    usage = k.resource_usage(p.id)
    assert usage.get("tool_calls", {}).get("used") == 1
    ok(f"resource_charge tool_calls used=1")

    # C: global RunGate queue drives execution (grant → queue → wake)
    k.run_gate_set_max(1)
    p_b = __import__("asyncio").run(
        k.create_process(
            "p0_smoke_b",
            session_id="smoke-s1b",
            capabilities=["file_read"],
            intent={"goal": "t", "capabilities": ["file_read"], "constraints": {}},
        )
    )
    # p already holds a gate via run_acquire; second should queue
    g2 = k.run_gate_try(p_b.id, priority_class="foreground")
    if g2.get("status") != "queued":
        # if run_acquire didn't hold gate the same way, force: release and re-try with max=1
        k.run_gate_release(p.id)
        g_a = k.run_gate_try(p.id, priority_class="background")
        assert g_a.get("status") == "granted", g_a
        g2 = k.run_gate_try(p_b.id, priority_class="foreground")
    assert g2.get("status") == "queued", g2
    rid = g2.get("request_id")
    k.run_gate_release(p.id)
    polled = k.run_gate_poll(str(rid))
    assert polled.get("status") == "granted", polled
    k.run_gate_release(p_b.id)
    k.run_gate_set_max(4)
    __import__("asyncio").run(k.end_process(p_b.id, state="completed", reason="smoke_gate"))
    ok("run_gate queue/wake")

    # C: LLM admission
    k._call(
        "llm_set_config",
        {"max_in_flight": 1, "owner_reserve": 0, "queue_max": 4},
    )
    g1 = k._call("llm_try_acquire", {"source": "chat", "priority": 100, "request_id": "sm1"})
    assert g1.get("status") == "granted", g1
    g2 = k._call("llm_try_acquire", {"source": "workforce", "priority": 30, "request_id": "sm2"})
    assert g2.get("status") == "queued", g2
    k._call("llm_release", {"request_id": "sm1"})
    polled = k._call("llm_poll", {"request_id": "sm2"})
    assert polled.get("status") == "granted", polled
    k._call("llm_release", {"request_id": "sm2"})
    ok("llm admission grant/queue/release")

    # D: decide_tool secret + ask
    dec = k.decide_tool(
        "file_read",
        {"path": str(Path.cwd() / ".env")},
        process_id=p.id,
        emit=True,
    )
    assert dec.get("verdict") == "deny" and dec.get("layer") == "secret_floor", dec
    ok("decide_tool secret_floor")

    # expand for write ask test
    k.apply_intent(
        p.id,
        {
            "goal": "write",
            "capabilities": ["file_write"],
            "constraints": {"allow_risky": True},
        },
    )
    ask = k.decide_tool(
        "file_write",
        {"path": "a.txt", "content": "x"},
        process_id=p.id,
        emit=True,
    )
    assert ask.get("verdict") == "ask", ask
    ok("decide_tool write -> ask")

    # D: isolation
    k._call("isolation_set_profile", {"process_id": p.id, "profile": "untrusted"})
    try:
        k._call(
            "isolation_spawn",
            {"process_id": p.id, "command": "echo", "backend": "local"},
        )
        fail("untrusted should reject local backend")
    except Exception:
        ok("isolation untrusted rejects local")

    # D: checkpoint
    with tempfile.TemporaryDirectory() as td:
        fpath = str(Path(td) / "x.txt")
        Path(fpath).write_text("v1", encoding="utf-8")
        cp = k._call("checkpoint_begin", {"process_id": p.id, "path": fpath})
        assert cp.get("id")
        Path(fpath).write_text("v2", encoding="utf-8")
        k._call("checkpoint_restore", {"checkpoint_id": cp["id"]})
        assert Path(fpath).read_text(encoding="utf-8") == "v1"
        ok("checkpoint begin/restore")

    # D: trail
    trail = k.export_decision_trail(p.id)
    kinds = {e.get("kind") for e in trail.get("events") or []}
    if "policy.decision" not in kinds and "mediation" not in kinds:
        fail(f"empty trail kinds={kinds}")
    ok(f"decision trail events={trail.get('total')} kinds={sorted(kinds)}")

    # chain
    chain_ok, idx = k.verify_event_chain()
    assert chain_ok, f"chain break {idx}"
    ok("hash chain verified")

    k.run_release(p.id)
    __import__("asyncio").run(k.end_process(p.id, state="completed", reason="smoke_p0"))
    ok("end_process")

    # get_kernel path
    from backend.kernel import get_kernel, get_kernel_backend
    from backend.kernel.kernel import reset_kernel_for_tests

    reset_kernel_for_tests()
    os.environ["TAKTON_KERNEL_AUTO_START"] = "0"  # already up
    gk = get_kernel()
    backend = get_kernel_backend()
    if backend != "rust":
        fail(f"get_kernel backend={backend}, expected rust")
    ok(f"get_kernel() backend={backend}")

    print("=== P0 Integration Smoke PASSED ===")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as e:
        print(f"FAIL: exception {type(e).__name__}: {e}")
        raise SystemExit(1) from e
