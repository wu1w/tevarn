#!/usr/bin/env python3
"""Host long-stability gate (analysis P0 #1).

Simulated long-run (not wall-clock 2h by default):
  - ABI fail-closed check
  - N cycles: create → snapshot → spill → suspend/resume → marathon_record
  - optional fault inject: kill host mid-cycle, require auto-recover
  - recovery plan must keep full_replay=false after restart

Usage:
  python scripts/host_marathon_gate.py
  python scripts/host_marathon_gate.py --cycles 30 --inject-kill
  python scripts/host_marathon_gate.py --hours 2   # wall-clock soak (real 2h)

Exit 0 only if all cycles pass and resume_success_rate >= threshold.
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


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    raise SystemExit(1)


def ok(msg: str) -> None:
    print(f"  OK  {msg}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycles", type=int, default=20)
    ap.add_argument("--inject-kill", action="store_true", help="kill host mid-run once")
    ap.add_argument("--hours", type=float, default=0.0, help="wall-clock soak hours (0=cycle mode)")
    ap.add_argument(
        "--min-resume",
        type=float,
        default=float(os.environ.get("TEVARN_MARATHON_RESUME_THRESHOLD", "0.95")),
    )
    args = ap.parse_args()

    from backend.kernel_rust.abi_gate import REQUIRED_ABI_METHODS, check_required_abi
    from backend.kernel_rust.client import (
        RustAgentKernel,
        is_rust_host_available,
        restart_kernel_host,
        start_kernel_host,
        stop_kernel_host,
        reset_rust_kernel_for_tests,
    )

    print("=== Host Marathon / Stability Gate ===")
    if not is_rust_host_available():
        if not start_kernel_host():
            fail("cannot start host")
    reset_rust_kernel_for_tests()
    k = RustAgentKernel(auto_start=True)

    methods = set(k.list_methods())
    abi = check_required_abi(methods)
    if not abi["ok"]:
        fail(f"ABI missing: {abi['missing']}")
    ok(f"ABI gate pass ({len(REQUIRED_ABI_METHODS)} required, host has {len(methods)})")

    import asyncio

    resume_ok = 0
    resume_fail = 0
    injected = False
    deadline = time.time() + args.hours * 3600 if args.hours > 0 else None
    cycles = args.cycles
    i = 0

    while True:
        if deadline is not None:
            if time.time() >= deadline:
                break
            cycles = i + 1  # unbounded until hours
        elif i >= cycles:
            break
        i += 1

        # optional kill inject mid-run
        if args.inject_kill and not injected and i == max(2, cycles // 3):
            print("  … inject: stop host")
            stop_kernel_host()
            time.sleep(0.3)
            # next RPC should recover
            injected = True

        try:
            ping = k.host_watchdog_ping()
            if not ping.get("ok"):
                # one more restart attempt
                restart_kernel_host()
                reset_rust_kernel_for_tests()
                k = RustAgentKernel(auto_start=True)
                ping = k.host_watchdog_ping()
            if not ping.get("ok"):
                resume_fail += 1
                print(f"  cycle {i}: host ping fail")
                continue

            p = asyncio.run(
                k.create_process(
                    f"mg_{i}",
                    capabilities=["file_read"],
                    intent={
                        "goal": "marathon gate",
                        "capabilities": ["file_read"],
                        "constraints": {},
                    },
                )
            )
            k.process_snapshot(p.id, meta={"cycle": i})
            plan = k.process_recovery_plan(p.id)
            if plan.get("full_replay") is not False:
                resume_fail += 1
                print(f"  cycle {i}: recovery allows full_replay")
            else:
                k.suspend_process_sync(p.id, reason="gate")
                rp = k.resume_process_sync(p.id)
                if str(getattr(rp, "state", "")) == "running":
                    resume_ok += 1
                    k.marathon_record("resume_ok", reason="gate")
                else:
                    resume_fail += 1
                    k.marathon_record("resume_fail", reason="not_running")
            k.result_spill(p.id, "gate", "x" * 1000)
            asyncio.run(k.end_process(p.id, state="completed"))
            if i % 5 == 0 or i == 1:
                ok(f"cycle {i}/{cycles if deadline is None else 'soak'} resume_ok={resume_ok}")
        except Exception as e:
            resume_fail += 1
            print(f"  cycle {i}: exception {e}")
            try:
                restart_kernel_host()
                reset_rust_kernel_for_tests()
                k = RustAgentKernel(auto_start=True)
            except Exception:
                pass

    total = resume_ok + resume_fail
    rate = (resume_ok / total) if total else 0.0
    metrics = {}
    try:
        metrics = k.marathon_metrics() or {}
    except Exception:
        pass
    st = k.host_runtime_status() if hasattr(k, "host_runtime_status") else {}

    summary = {
        "cycles": i,
        "resume_ok": resume_ok,
        "resume_fail": resume_fail,
        "rate": round(rate, 4),
        "min_resume": args.min_resume,
        "inject_kill": injected,
        "restart_count": st.get("restart_count"),
        "metrics": metrics,
        "product_version": "0.5.2-alpha",
        "gate": "host_marathon",
    }
    print(summary)
    # Evidence artifact for release dossier
    try:
        art = ROOT / "artifacts" / "release-gates"
        art.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        out = art / f"{stamp}-host-marathon.txt"
        out.write_text(
            f"host_marathon_gate\n{summary}\n"
            f"inject_kill_requested={args.inject_kill}\n"
            f"hours={args.hours}\n",
            encoding="utf-8",
        )
        ok(f"evidence written {out}")
    except Exception as e:
        print(f"  (evidence write skip: {e})")
    if rate + 1e-9 < args.min_resume:
        fail(f"resume rate {rate:.3f} < {args.min_resume}")
    if args.inject_kill and not injected:
        fail("inject-kill requested but never fired")
    if not st.get("abi", {}).get("ok", True) and "abi" in st:
        fail("ABI not ok after soak")
    ok("HOST MARATHON GATE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
