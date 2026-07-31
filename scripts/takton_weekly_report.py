#!/usr/bin/env python3
"""AIOS 债 #2：生成观测 / Eval 周报。

用法：
  python scripts/takton_weekly_report.py
  python scripts/takton_weekly_report.py --run-eval
  python scripts/takton_weekly_report.py --week 2026-W31
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TAKTON_KERNEL_BACKEND", "rust")
os.environ.setdefault("TAKTON_KERNEL_AUTO_START", "1")


def main() -> int:
    ap = argparse.ArgumentParser(description="Takton weekly observability report")
    ap.add_argument("--run-eval", action="store_true", help="run eval harness first")
    ap.add_argument("--week", default=None, help="load existing week id (e.g. 2026-W31)")
    ap.add_argument("--no-persist", action="store_true")
    args = ap.parse_args()

    from backend.services.weekly_report import (
        collect_weekly_report,
        load_weekly_report,
    )

    if args.week:
        rep = load_weekly_report(args.week)
        if not rep:
            print(json.dumps({"error": f"no report for {args.week}"}, indent=2))
            return 1
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0

    k = None
    try:
        from backend.kernel_rust.client import (
            RustAgentKernel,
            is_rust_host_available,
            start_kernel_host,
        )

        for _ in range(10):
            try:
                if not is_rust_host_available():
                    start_kernel_host()
                k = RustAgentKernel(auto_start=True)
                break
            except Exception:
                time.sleep(0.2)
        if k is None:
            print("warn: kernel host unavailable; report will use disk eval only", file=sys.stderr)
    except Exception as e:
        print(f"warn: kernel host: {e}", file=sys.stderr)

    rep = collect_weekly_report(
        k,
        run_eval=bool(args.run_eval),
        persist=not args.no_persist,
    )
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    health = (rep.get("health") or {}).get("overall")
    ev = (rep.get("eval") or {}).get("overall") if isinstance(rep.get("eval"), dict) else None
    print(
        f"=== Weekly Report week={rep.get('week')} health={health} eval={ev} ===",
        file=sys.stderr,
    )
    # soft gate: if eval present and failed, exit 1
    if isinstance(rep.get("eval"), dict) and rep["eval"].get("pass") is False:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
