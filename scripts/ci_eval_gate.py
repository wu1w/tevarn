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
    ap.add_argument("--min-overall", type=float, default=float(os.environ.get("TAKTON_EVAL_THRESHOLD", "0.75")))
    ap.add_argument("--max-health-drop", type=float, default=0.2)
    ap.add_argument("--require-eval", action="store_true", help="fail if no eval artifact")
    args = ap.parse_args()

    from backend.services.weekly_report import (
        collect_weekly_report,
        load_latest_eval,
        load_weekly_report,
        previous_week_report,
    )

    eval_result = load_latest_eval()
    if args.run:
        os.environ.setdefault("TAKTON_KERNEL_BACKEND", "rust")
        os.environ.setdefault("TAKTON_KERNEL_AUTO_START", "1")
        from scripts.takton_eval import (
            _connect,
            suite_coding,
            suite_long,
            suite_research,
            suite_safety,
        )
        from backend.services.weekly_report import persist_eval_run

        k = _connect()
        suites = [suite_coding(k), suite_research(k), suite_long(k), suite_safety(k)]
        overall = sum(s["score"] for s in suites) / max(1, len(suites))
        eval_result = {
            "overall": round(overall, 4),
            "threshold": args.min_overall,
            "suites": suites,
            "pass": overall + 1e-9 >= args.min_overall,
        }
        persist_eval_run(eval_result)
        collect_weekly_report(k, eval_result=eval_result, persist=True)

    if eval_result is None:
        msg = "no eval artifact (run takton_eval.py or pass --run)"
        if args.require_eval:
            print(f"FAIL: {msg}")
            return 1
        print(f"WARN: {msg} — soft pass")
        return 0

    overall = float(eval_result.get("overall") or 0)
    if overall + 1e-9 < args.min_overall:
        print(json.dumps({"fail": "eval_overall", "overall": overall, "min": args.min_overall}, indent=2))
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
