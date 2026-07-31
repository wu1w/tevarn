#!/usr/bin/env python3
"""P1-B G5: Eval Harness v1 — four fixed suites (coding / research / long / safety).

Runs against Rust kernel host (no live LLM required for kernel-path checks).
Exit 0 if overall score >= threshold.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TAKTON_KERNEL_BACKEND", "rust")
os.environ.setdefault("TAKTON_KERNEL_AUTO_START", "1")


def _connect():
    from backend.kernel_rust.client import (
        RustAgentKernel,
        is_rust_host_available,
        reset_rust_kernel_for_tests,
        start_kernel_host,
    )

    reset_rust_kernel_for_tests()
    for _ in range(10):
        try:
            if not is_rust_host_available():
                start_kernel_host()
            return RustAgentKernel(auto_start=True)
        except Exception:
            time.sleep(0.2)
    raise SystemExit("cannot connect host")


def suite_coding(k) -> dict:
    """Coding: profile apply + skill gate + edit session (P2 H5 weekly score)."""
    import asyncio
    import tempfile

    p = asyncio.run(
        k.create_process(
            "eval_code",
            capabilities=["file_read"],
            intent={"goal": "code", "capabilities": ["file_read"], "constraints": {}},
        )
    )
    score = 0.0
    ap = k._call("coding_profile_apply", {"process_id": p.id, "profile": "engineering"}) or {}
    score += 0.25 if ap.get("ok") else 0.0
    tools = k.filter_tools(p.id, ["file_read", "file_write", "terminal", "http"])
    score += 0.2 if "file_read" in tools else 0.0
    score += 0.15 if "file_write" in tools or "terminal" in tools else 0.0
    pkg = k.skill_register("eval_coder", "# coding helper\n", tests=["syntax_ok"])
    try:
        k.skill_verify(pkg["id"])
        k.skill_activate(pkg["id"])
        score += 0.2 if k.skill_is_loadable("eval_coder") else 0.0
    except Exception:
        pass
    try:
        with tempfile.TemporaryDirectory() as td:
            path = str(Path(td) / "f.txt")
            Path(path).write_text("x\n", encoding="utf-8")
            s = k._call(
                "edit_propose",
                {"process_id": p.id, "path": path, "after": "x\ny\n"},
            ) or {}
            if s.get("id"):
                k._call("edit_confirm", {"session_id": s["id"]})
                k._call("edit_rollback", {"session_id": s["id"]})
                score += 0.2
    except Exception:
        pass
    asyncio.run(k.end_process(p.id, state="completed"))
    return {"suite": "coding", "score": round(min(1.0, score), 3), "max": 1.0}


def suite_research(k) -> dict:
    """Research: readonly intent + memory layers."""
    import asyncio

    p = asyncio.run(
        k.create_process(
            "eval_research",
            capabilities=["file_read", "grep", "knowledge_search"],
            intent={
                "goal": "research",
                "capabilities": ["file_read", "grep", "knowledge_search"],
                "constraints": {},
            },
        )
    )
    score = 0.0
    tools = k.filter_tools(p.id, ["file_read", "terminal", "grep"])
    score += 0.4 if "terminal" not in tools else 0.0
    score += 0.3 if "file_read" in tools else 0.0
    k.memory_layer_put("researcher", "working", "finding-1", 0.9)
    c = k.memory_layer_consolidate("researcher")
    score += 0.3 if int(c.get("promoted_to_episodic") or 0) >= 1 else 0.0
    asyncio.run(k.end_process(p.id, state="completed"))
    return {"suite": "research", "score": round(score, 3), "max": 1.0}


def suite_long(k) -> dict:
    """Long-run: snapshot + suspend/resume + iteration budget."""
    import asyncio

    p = asyncio.run(
        k.create_process(
            "eval_long",
            capabilities=["file_read"],
            intent={"goal": "long", "capabilities": ["file_read"], "constraints": {}},
        )
    )
    score = 0.0
    k.iteration_set_budget(p.id, 5)
    for _ in range(3):
        k.iteration_consume(p.id)
    snap = k.process_snapshot(p.id, meta={"eval": "long"})
    score += 0.3 if snap.get("id") else 0.0
    plan = k.process_recovery_plan(p.id)
    score += 0.3 if plan.get("full_replay") is False else 0.0
    k.suspend_process_sync(p.id, reason="eval")
    rp = k.resume_process_sync(p.id)
    score += 0.4 if str(getattr(rp, "state", "")) == "running" else 0.0
    asyncio.run(k.end_process(p.id, state="completed"))
    return {"suite": "long", "score": round(score, 3), "max": 1.0}


def suite_safety(k) -> dict:
    """Safety: court secret deny + skill auto_apply false + untrusted isolation."""
    import asyncio

    p = asyncio.run(
        k.create_process(
            "eval_safe",
            capabilities=["file_read"],
            intent={"goal": "safe", "capabilities": ["file_read"], "constraints": {}},
        )
    )
    score = 0.0
    d = k.decide_tool(
        "file_read",
        {"path": str(Path.cwd() / ".env")},
        process_id=p.id,
        emit=False,
    )
    score += 0.35 if d.get("verdict") == "deny" else 0.0
    pol = k.evolution_policy()
    score += 0.35 if pol.get("auto_apply") is False else 0.0
    k._call("isolation_set_profile", {"process_id": p.id, "profile": "untrusted"})
    try:
        k._call(
            "isolation_spawn",
            {"process_id": p.id, "command": "echo", "backend": "local"},
        )
        score += 0.0
    except Exception:
        score += 0.3
    asyncio.run(k.end_process(p.id, state="completed"))
    return {"suite": "safety", "score": round(score, 3), "max": 1.0}


def main() -> int:
    threshold = float(os.environ.get("TAKTON_EVAL_THRESHOLD", "0.75") or 0.75)
    k = _connect()
    results = [
        suite_coding(k),
        suite_research(k),
        suite_long(k),
        suite_safety(k),
    ]
    overall = sum(r["score"] for r in results) / max(1, len(results))
    out = {
        "overall": round(overall, 4),
        "threshold": threshold,
        "suites": results,
        "pass": overall + 1e-9 >= threshold,
    }
    # 债 #2：持久化 eval 结果，供周报趋势
    try:
        from backend.services.weekly_report import persist_eval_run

        path = persist_eval_run(out)
        out["persisted"] = str(path)
    except Exception as e:
        out["persisted_error"] = str(e)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print(
        f"=== Eval Harness {'PASSED' if out['pass'] else 'FAILED'} "
        f"overall={overall:.3f} threshold={threshold} ==="
    )
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
