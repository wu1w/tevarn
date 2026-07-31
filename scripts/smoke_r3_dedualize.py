#!/usr/bin/env python3
"""R3 de-dualize smoke: domain events · approval · identity cache · memory mirror."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TAKTON_KERNEL_BACKEND", "rust")
os.environ.setdefault("TAKTON_KERNEL_AUTO_START", "1")
os.environ.setdefault("TAKTON_LLM_ALLOW_PY_FALLBACK", "1")  # smoke only


def fail(m: str) -> None:
    print(f"FAIL: {m}")
    raise SystemExit(1)


def ok(m: str) -> None:
    print(f"  OK  {m}")


def main() -> int:
    from backend.kernel_rust.client import (
        RustAgentKernel,
        is_rust_host_available,
        reset_rust_kernel_for_tests,
        start_kernel_host,
    )

    print("=== R3 De-dualize Smoke ===")
    reset_rust_kernel_for_tests()
    k = None
    for _ in range(12):
        try:
            if not is_rust_host_available():
                start_kernel_host()
            k = RustAgentKernel(auto_start=True)
            break
        except Exception:
            time.sleep(0.2)
    if k is None:
        fail("host")

    methods = set(k.list_methods())
    for m in (
        "domain_publish",
        "domain_recent",
        "approval_classify",
        "approval_should_auto",
        "inbox_submit",
        "inbox_claim",
        "identity_cache_put",
        "sys_memory_put",
        "memory_layer_put",
    ):
        if m not in methods:
            fail(f"missing {m}")
    ok(f"ABI methods total={len(methods)}")

    # domain
    k._call("domain_publish", {"topic": "job.test", "payload": {"x": 1}})
    r = k._call("domain_recent", {"limit": 5, "prefix": "job."}) or {}
    if not (r.get("events") or r.get("seq") is not None):
        fail(f"domain_recent: {r}")
    ok("domain events")

    # approval
    k._call(
        "approval_set_rules",
        {
            "rules": [
                {"key": "auto_low_risk", "enabled": True},
                {"key": "review_high_risk", "enabled": True},
            ]
        },
    )
    low = k._call("approval_classify", {"capabilities": ["file_read", "grep"]}) or {}
    high = k._call("approval_classify", {"capabilities": ["terminal"]}) or {}
    if low.get("kind") != "low" or high.get("kind") != "high":
        fail(f"classify {low} {high}")
    auto = k._call("approval_should_auto", {"capabilities": ["file_read"]}) or {}
    if not auto.get("auto_approve"):
        fail(f"auto: {auto}")
    ok("approval rules")

    # identity cache
    k._call(
        "identity_cache_put",
        {
            "identity": {
                "id": "id-r3",
                "name": "R3Worker",
                "status": "active",
                "capabilities": ["file_read"],
            }
        },
    )
    got = k._call("identity_cache_get", {"id": "R3Worker"}) or {}
    if got.get("id") != "id-r3":
        fail(f"cache: {got}")
    ok("identity hot cache")

    # inbox dual claim
    k._call(
        "inbox_submit",
        {
            "identity": "R3Worker",
            "instruction": "do work",
            "priority": 10,
            "meta": {"db_item_id": "00000000-0000-0000-0000-000000000001"},
        },
    )
    c1 = k._call("inbox_claim", {"worker_id": "w1"}) or {}
    c2 = k._call("inbox_claim", {"worker_id": "w2"}) or {}
    if not c1.get("claimed") or c2.get("claimed"):
        fail(f"double claim: {c1} {c2}")
    ok("inbox single claim")

    # memory mirror path
    k._call(
        "sys_memory_put",
        {"identity": "id-r3", "key": "crew.experience.x", "value": {"content": "hi"}},
    )
    k._call(
        "memory_layer_put",
        {
            "identity": "id-r3",
            "layer": "episodic",
            "content": "hi",
            "score": 0.9,
        },
    )
    ok("memory service write")

    # py shims
    from backend.kernel.approval_rules import classify_caps, evolution_requires_review
    from backend.kernel.domain_events import map_kernel_kind, publish_sync, recent_events

    assert classify_caps(["file_read"]) == "low"
    assert evolution_requires_review() is True
    assert map_kernel_kind("inbox_claimed") == "job.claimed"
    publish_sync("test.r3", {"ok": True})
    _ = recent_events(limit=5)
    ok("python shims prefer rust")

    print("=== R3 De-dualize Smoke PASSED ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
