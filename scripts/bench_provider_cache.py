#!/usr/bin/env python3
"""Bench prompt-cache hit rate for the active (or specified) LLM provider.

Usage (from repo root, venv active):
  python scripts/bench_provider_cache.py
  python scripts/bench_provider_cache.py --rounds 5 --out reports/cache_bench.json

Sends a long stable system + tools prefix for N rounds and prints
cache_read / prompt / billable from normalized usage.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


STABLE_SYSTEM = (
    "You are a Takton cache-bench assistant. " * 40
    + "Rules: reply with a single short word. Do not call tools."
)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "noop_tool",
            "description": "A dummy tool used only to enlarge the stable tools prefix for cache tests. " * 8,
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "string", "description": "unused"},
                },
            },
        },
    }
]


async def run_rounds(rounds: int) -> dict:
    from backend.services.llm.factory import LLMServiceFactory
    from backend.services.llm.provider_profiles import resolve_profile, profile_as_dict
    from backend.core.config import settings

    svc = LLMServiceFactory.get_service()
    profile = getattr(svc, "profile", None) or resolve_profile(
        base_url=getattr(svc, "base_url", None),
        model=getattr(svc, "model", None),
        llm_provider=settings.llm_provider,
    )

    results = []
    for i in range(rounds):
        messages = [
            {"role": "system", "content": STABLE_SYSTEM},
            {"role": "user", "content": f"bench round {i + 1}: say ok"},
        ]
        usage = {}
        t0 = time.time()
        async for chunk in svc.chat(messages, tools=TOOLS, stream=False):
            u = getattr(chunk, "usage", None)
            if isinstance(u, dict) and u:
                usage.update(u)
        elapsed = time.time() - t0
        results.append(
            {
                "round": i + 1,
                "elapsed_sec": round(elapsed, 3),
                "usage": usage,
                "cache_read": int(usage.get("cache_read_input_tokens") or 0),
                "prompt": int(usage.get("prompt_tokens") or 0),
                "billable": int(usage.get("billable_tokens") or 0),
            }
        )
        print(
            f"round={i + 1} prompt={results[-1]['prompt']} "
            f"cache_read={results[-1]['cache_read']} "
            f"billable={results[-1]['billable']} "
            f"t={elapsed:.2f}s"
        )

    hits = [r for r in results if r["cache_read"] > 0]
    report = {
        "model": getattr(svc, "model", None),
        "base_url": getattr(svc, "base_url", None),
        "profile": profile_as_dict(profile),
        "rounds": results,
        "summary": {
            "rounds_with_cache_hit": len(hits),
            "hit_rate": len(hits) / max(1, len(results)),
            "avg_prompt": sum(r["prompt"] for r in results) / max(1, len(results)),
            "avg_billable": sum(r["billable"] for r in results) / max(1, len(results)),
        },
    }
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--out", type=str, default="reports/cache_bench.json")
    args = ap.parse_args()

    try:
        report = asyncio.run(run_rounds(args.rounds))
    except Exception as e:
        print(f"bench failed: {e}", file=sys.stderr)
        return 1

    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
