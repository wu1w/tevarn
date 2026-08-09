#!/usr/bin/env python3
"""P0.5 marathon soak — resume success rate under simulated long-run stress.

Default is a **compressed** soak suitable for CI (many resume cycles, no wall 2h).
For a longer wall-clock soak:

  $env:MARATHON_SOAK_SECONDS = "7200"   # 2 hours
  $env:MARATHON_CYCLES = "120"
  python scripts/marathon_soak.py

Exit 0 when marathon_resume_success >= threshold (default 0.95).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TEVARN_KERNEL_BACKEND", "rust")
os.environ.setdefault("TEVARN_KERNEL_AUTO_START", "1")


def main() -> int:
    ap = argparse.ArgumentParser(description="P0.5 marathon soak")
    ap.add_argument(
        "--cycles",
        type=int,
        default=int(os.environ.get("MARATHON_CYCLES", "40") or 40),
        help="resume cycles (snapshot→suspend→resume→recover)",
    )
    ap.add_argument(
        "--threshold",
        type=float,
        default=float(os.environ.get("MARATHON_RESUME_THRESHOLD", "0.95") or 0.95),
        help="min marathon_resume_success rate",
    )
    ap.add_argument(
        "--seconds",
        type=float,
        default=float(os.environ.get("MARATHON_SOAK_SECONDS", "0") or 0),
        help="optional wall-clock minimum duration (0 = pure cycle mode)",
    )
    ap.add_argument(
        "--sleep",
        type=float,
        default=float(os.environ.get("MARATHON_CYCLE_SLEEP", "0.02") or 0.02),
        help="sleep between cycles (stretch soak)",
    )
    args = ap.parse_args()

    from backend.kernel_rust.client import (
        RustAgentKernel,
        is_rust_host_available,
        reset_rust_kernel_for_tests,
        start_kernel_host,
    )

    print("=== P0.5 Marathon Soak ===")
    print(
        f"cycles={args.cycles} threshold={args.threshold} "
        f"wall_seconds={args.seconds} sleep={args.sleep}"
    )
    reset_rust_kernel_for_tests()
    k = None
    last_err: Exception | None = None
    for attempt in range(10):
        try:
            if not is_rust_host_available():
                if not start_kernel_host():
                    time.sleep(0.2)
                    continue
            k = RustAgentKernel(auto_start=True)
            break
        except Exception as e:
            last_err = e
            time.sleep(0.25)
    if k is None:
        print(f"FAIL: cannot start/connect host: {last_err}")
        return 1

    import asyncio

    t0 = time.time()
    success = 0
    fail = 0
    parent = asyncio.run(
        k.create_process(
            "soak_parent",
            session_id="soak-s1",
            capabilities=["file_read", "terminal"],
            intent={
                "goal": "marathon",
                "capabilities": ["file_read", "terminal"],
                "constraints": {"allow_risky": True},
            },
        )
    )
    asyncio.run(k.mark_running(parent.id))
    k.marathon_record("attempt", reason="soak_start")
    k.iteration_set_budget(parent.id, max(args.cycles * 2, 50))

    for i in range(1, args.cycles + 1):
        k.marathon_record("attempt", reason=f"cycle_{i}")
        ok = True
        reason = "ok"
        try:
            # simulate work
            k.iteration_consume(parent.id)
            snap = k.process_snapshot(parent.id, meta={"cycle": i, "soak": True})
            if not snap.get("id"):
                ok = False
                reason = "snapshot_missing"
            else:
                k.marathon_record("snapshot_ok", reason=str(snap.get("id")))

            # interruptible
            k.suspend_process_sync(parent.id, reason=f"soak_cycle_{i}")
            rp = k.resume_process_sync(parent.id)
            if str(getattr(rp, "state", "")) != "running":
                ok = False
                reason = f"resume_state={getattr(rp, 'state', None)}"

            plan = k.process_recovery_plan(parent.id)
            if plan.get("full_replay") is True:
                ok = False
                reason = "full_replay_true"
            elif plan.get("mode") not in ("snapshot_plus_incremental", "none"):
                # after first snap must be incremental
                if i > 1 and plan.get("mode") != "snapshot_plus_incremental":
                    ok = False
                    reason = f"bad_mode={plan.get('mode')}"

            # spill pressure
            if i % 5 == 0:
                big = ("x" * 100) * 50
                sp = k.result_spill(parent.id, "command", big)
                if not sp.get("spilled"):
                    # threshold may skip; not a hard fail
                    pass

            # cost/cache noise
            if i % 3 == 0:
                k.cache_record("soak_provider", hit=True, bytes_saved=64)
                k.cost_charge(parent.id, "soak_provider", 10, 8)
            else:
                k.cache_record("soak_provider", hit=False, bytes_saved=0)
                k.cost_charge(parent.id, "soak_provider", 12, 12)

        except Exception as e:
            ok = False
            reason = str(e)[:120]

        if ok:
            success += 1
            k.marathon_record("resume_ok", reason=reason)
        else:
            fail += 1
            k.marathon_record("resume_fail", reason=reason)
            print(f"  cycle {i} FAIL: {reason}")

        if args.sleep > 0:
            time.sleep(args.sleep)

        # wall clock stretch
        if args.seconds > 0 and (time.time() - t0) >= args.seconds:
            print(f"  wall clock reached {args.seconds}s at cycle {i}")
            break

    # optional: wait remaining wall time with light keep-alive
    if args.seconds > 0:
        while (time.time() - t0) < args.seconds:
            time.sleep(min(5.0, max(0.1, args.seconds - (time.time() - t0))))
            try:
                k.process_snapshot(parent.id, meta={"keepalive": True})
            except Exception:
                pass

    metrics = k.marathon_metrics()
    rate = float(metrics.get("marathon_resume_success") or 0.0)
    cost = k.cost_panel()
    cache = k.cache_metrics()
    print(
        f"result success={success} fail={fail} "
        f"marathon_resume_success={rate:.4f} "
        f"threshold={args.threshold} "
        f"elapsed={time.time() - t0:.1f}s"
    )
    print(f"  metrics={metrics}")
    print(f"  cost_totals={(cost.get('totals') or {})}")
    print(f"  cache_totals={(cache.get('totals') or {})}")

    k.reclaim_process_tree(parent.id, reason="soak_done")

    if rate + 1e-9 < args.threshold:
        print(f"FAIL: marathon_resume_success {rate:.4f} < {args.threshold}")
        return 1
    print("=== P0.5 Marathon Soak PASSED ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
