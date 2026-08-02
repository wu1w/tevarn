#!/usr/bin/env python3
"""P0.5 marathon smoke — simulate long-run reliability without 2h wall clock.

Exercises:
  snapshot → spill → iteration budget → doom → suspend/resume → reclaim
  → cache metrics → recovery plan (no full replay)

Exit 0 on success. Not a real 2h soak; gates the marathon_resume path.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TAKTON_KERNEL_BACKEND", "rust")
os.environ.setdefault("TAKTON_KERNEL_AUTO_START", "1")


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    raise SystemExit(1)


def ok(msg: str) -> None:
    print(f"  OK  {msg}")


def main() -> int:
    from backend.kernel_rust.client import (
        RustAgentKernel,
        is_rust_host_available,
        reset_rust_kernel_for_tests,
        start_kernel_host,
    )

    print("=== P0.5 Marathon Path Smoke ===")
    if not is_rust_host_available():
        if not start_kernel_host():
            fail("cannot start kernel host")
    reset_rust_kernel_for_tests()
    k = RustAgentKernel(auto_start=True)

    methods = set(k.list_methods())
    need = {
        "process_snapshot",
        "process_recovery_plan",
        "result_spill",
        "iteration_consume",
        "doom_record",
        "cache_metrics",
        "reclaim_process_tree",
    }
    missing = need - methods
    if missing:
        fail(f"ABI missing P0.5 methods: {missing}")
    ok(f"ABI has P0.5 methods (+{len(need)})")

    import asyncio

    parent = asyncio.run(
        k.create_process(
            "marathon_parent",
            session_id="marathon-s1",
            capabilities=["file_read", "terminal"],
            intent={
                "goal": "long run",
                "capabilities": ["file_read", "terminal"],
                "constraints": {"allow_risky": True},
            },
        )
    )
    child = asyncio.run(
        k.create_process(
            "marathon_child",
            parent_id=parent.id,
            capabilities=["file_read"],
            intent={"goal": "sub", "capabilities": ["file_read"], "constraints": {}},
        )
    )
    asyncio.run(k.mark_running(parent.id))
    ok("parent+child created")

    # Simulated iterations with periodic snapshots
    k.iteration_set_budget(parent.id, 20)
    for i in range(1, 13):
        st = k.iteration_consume(parent.id)
        if st.get("status") != "allow":
            fail(f"unexpected iteration status at {i}: {st}")
        if i % 4 == 0:
            snap = k.process_snapshot(parent.id, meta={"iter": i, "phase": "sim"})
            if not snap.get("id"):
                fail(f"snapshot failed at iter {i}")
    ok("12 iterations + 3 snapshots")

    plan = k.process_recovery_plan(parent.id)
    if plan.get("full_replay") is not False:
        fail(f"recovery must not full-replay: {plan}")
    if plan.get("mode") != "snapshot_plus_incremental":
        fail(f"bad recovery mode: {plan}")
    ok(f"recovery plan tail_hash={str(plan.get('tail_hash') or '')[:12]}…")

    # Large result spill
    big = ("line\n" * 2000)
    spill = k.result_spill(parent.id, "command", big)
    if not spill.get("spilled"):
        fail(f"expected spill: {spill}")
    hid = (spill.get("handle") or {}).get("id")
    loaded = k.result_load(str(hid), process_id=parent.id)
    if loaded.get("content") != big:
        fail("spill load mismatch")
    ok(f"result spill/load bytes={len(big)}")

    # Doom loop
    for _ in range(3):
        d = k.doom_record(parent.id, "grep", {"pattern": "x", "path": "."})
    if d.get("status") != "doom_loop":
        fail(f"expected doom_loop: {d}")
    ok("doom_loop trips")

    # Suspend / resume (interruptible long run)
    sp = k.suspend_process_sync(parent.id, reason="user interrupt")
    if str(getattr(sp, "state", "")) != "suspended":
        fail(f"suspend failed: {sp}")
    rp = k.resume_process_sync(parent.id)
    if str(getattr(rp, "state", "")) != "running":
        fail(f"resume failed: {rp}")
    ok("suspend/resume")

    # Cache metrics
    k.cache_record("anthropic", hit=True, bytes_saved=2048)
    k.cache_record("anthropic", hit=False)
    m = k.cache_metrics()
    if not (m.get("families") or {}).get("anthropic"):
        fail(f"cache metrics empty: {m}")
    ok(f"cache_hit_rate totals={m.get('totals')}")

    # Cost + cache panel path
    k.cache_record("anthropic", hit=True, bytes_saved=100)
    k.cost_charge(parent.id, "anthropic", 50, 40)
    cost = k.cost_panel()
    if int((cost.get("totals") or {}).get("tokens") or 0) < 50:
        fail(f"cost_panel missing charge: {cost}")
    ok(f"cost_panel tokens={(cost.get('totals') or {}).get('tokens')}")

    # Marathon metrics path (resume success rate)
    k.marathon_record("attempt", reason="smoke")
    k.marathon_record("resume_ok", reason="smoke")
    k.marathon_record("resume_ok", reason="smoke")
    mm = k.marathon_metrics()
    if float(mm.get("marathon_resume_success") or 0) < 0.99:
        fail(f"expected resume success rate 1.0: {mm}")
    ok(f"marathon_resume_success={mm.get('marathon_resume_success')}")

    # Reclaim tree
    rec = k.reclaim_process_tree(parent.id, reason="marathon_done")
    if int(rec.get("reclaimed") or 0) < 1:
        fail(f"reclaim expected children: {rec}")
    ok(f"reclaim_process_tree reclaimed={rec.get('reclaimed')}")

    chain_ok, idx = k.verify_event_chain()
    if not chain_ok:
        fail(f"hash chain break at {idx}")
    ok("hash chain verified")

    print("=== P0.5 Marathon Path Smoke PASSED ===")
    print("Hint: full soak → python scripts/marathon_soak.py --cycles 40")
    print("      2h wall  → $env:MARATHON_SOAK_SECONDS=7200; python scripts/marathon_soak.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
