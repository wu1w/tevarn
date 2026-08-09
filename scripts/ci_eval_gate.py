#!/usr/bin/env python3
"""T10：Eval / 周报 CI 门禁。

- 优先读已有 data/eval 结果（不强制起 host）
- --run 时才连接 host 跑全量 eval（CI 可选）
- 周同比 health 跌破阈值则 fail

Usage:
  python scripts/ci_eval_gate.py
  python scripts/ci_eval_gate.py --run
  python scripts/ci_eval_gate.py --min-overall 0.75 --max-health-drop 0.15
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true", help="run live eval against host")
    ap.add_argument("--min-overall", type=float, default=float(os.environ.get("TEVARN_EVAL_THRESHOLD", "0.75")))
    ap.add_argument(
        "--min-marathon",
        type=float,
        default=float(os.environ.get("TEVARN_MARATHON_RESUME_THRESHOLD", "0.95")),
        help="hard floor for marathon_resume_success (0.7 productization)",
    )
    ap.add_argument("--max-health-drop", type=float, default=0.2)
    ap.add_argument("--require-eval", action="store_true", help="fail if no eval artifact")
    ap.add_argument(
        "--require-marathon",
        action="store_true",
        default=os.environ.get("TEVARN_REQUIRE_MARATHON", "").strip() in ("1", "true", "yes"),
        help="fail when marathon metrics missing (hard gate mode)",
    )
    args = ap.parse_args()

    from backend.services.weekly_report import (
        collect_weekly_report,
        load_latest_eval,
        load_weekly_report,
        previous_week_report,
    )

    eval_result = load_latest_eval()
    if args.run:
        os.environ.setdefault("TEVARN_KERNEL_BACKEND", "rust")
        os.environ.setdefault("TEVARN_KERNEL_AUTO_START", "1")
        # Delegate to full harness (includes marathon hard gate + kernel ledger)
        from scripts.tevarn_eval import main as eval_main

        rc = eval_main()
        eval_result = load_latest_eval()
        if rc != 0:
            print("FAIL: live tevarn_eval hard gate")
            return rc
        if eval_result is not None:
            try:
                from backend.kernel_rust.client import RustAgentKernel, is_rust_host_available, start_kernel_host

                if not is_rust_host_available():
                    start_kernel_host()
                k = RustAgentKernel(auto_start=True)
                collect_weekly_report(k, eval_result=eval_result, persist=True)
            except Exception as e:
                print(f"WARN: weekly collect skipped: {e}")

    if eval_result is None:
        msg = "no eval artifact (run tevarn_eval.py or pass --run)"
        if args.require_eval:
            print(f"FAIL: {msg}")
            return 1
        print(f"WARN: {msg} — soft pass")
        return 0

    overall = float(eval_result.get("overall") or 0)
    if overall + 1e-9 < args.min_overall:
        print(json.dumps({"fail": "eval_overall", "overall": overall, "min": args.min_overall}, indent=2))
        return 1

    # Marathon hard gate
    marathon_rate = eval_result.get("marathon_resume_success")
    if marathon_rate is None:
        for s in eval_result.get("suites") or []:
            if isinstance(s, dict) and s.get("suite") == "long":
                marathon_rate = s.get("marathon_resume_success")
                if marathon_rate is None and isinstance(s.get("marathon_metrics"), dict):
                    marathon_rate = s["marathon_metrics"].get("marathon_resume_success")
                break
    if marathon_rate is None:
        if args.require_marathon or args.require_eval:
            print(json.dumps({"fail": "marathon_missing", "min": args.min_marathon}, indent=2))
            return 1
        print("WARN: marathon_resume_success missing — soft skip marathon hard gate")
        marathon_rate = None
    else:
        marathon_rate = float(marathon_rate)
        if marathon_rate + 1e-9 < args.min_marathon:
            print(
                json.dumps(
                    {
                        "fail": "marathon_resume_success",
                        "rate": marathon_rate,
                        "min": args.min_marathon,
                    },
                    indent=2,
                )
            )
            return 1

    weekly = load_weekly_report(None)
    trend_ok = True
    drop = None
    if weekly and isinstance(weekly.get("health"), dict):
        prev = previous_week_report(weekly.get("week"))
        if prev and isinstance(prev.get("health"), dict):
            cur_h = float(weekly["health"].get("overall") or 0)
            prev_h = float(prev["health"].get("overall") or 0)
            drop = round(prev_h - cur_h, 4)
            if drop > args.max_health_drop:
                trend_ok = False

    out = {
        "ok": trend_ok,
        "eval_overall": overall,
        "min_overall": args.min_overall,
        "marathon_resume_success": marathon_rate,
        "min_marathon": args.min_marathon,
        "health_drop": drop,
        "max_health_drop": args.max_health_drop,
        "week": (weekly or {}).get("week"),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    if not trend_ok:
        print("=== CI Eval Gate FAIL (health drop) ===")
        return 1
    print("=== CI Eval Gate PASSED ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
