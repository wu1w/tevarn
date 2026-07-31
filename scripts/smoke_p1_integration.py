#!/usr/bin/env python3
"""P1 end-to-end smoke: IPC · services · inbox · skill-gate · context · memory.

Exit 0 on success.
"""

from __future__ import annotations

import os
import sys
import time
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
        KernelPermissionError,
        RustAgentKernel,
        is_rust_host_available,
        reset_rust_kernel_for_tests,
        start_kernel_host,
    )

    print("=== P1 Integration Smoke ===")
    reset_rust_kernel_for_tests()
    k = None
    for _ in range(10):
        try:
            if not is_rust_host_available():
                start_kernel_host()
            k = RustAgentKernel(auto_start=True)
            break
        except Exception:
            time.sleep(0.2)
    if k is None:
        fail("cannot connect host")

    methods = set(k.list_methods())
    need = {
        "ipc_send",
        "ipc_recv",
        "service_list",
        "sys_memory_put",
        "sys_notify_push",
        "identity_cache_put",
        "inbox_claim",
        "skill_verify",
        "skill_activate",
        "evolution_policy",
        "context_put_page",
        "memory_layer_consolidate",
    }
    missing = need - methods
    if missing:
        fail(f"ABI missing: {missing}")
    ok(f"ABI P1 methods present (+{len(need)}) total={len(methods)}")

    import asyncio

    # two agents with ipc caps
    a = asyncio.run(
        k.create_process(
            "agent_a",
            capabilities=["file_read", "ipc_send", "ipc_recv", "ipc"],
            intent={
                "goal": "comm",
                "capabilities": ["file_read", "ipc_send", "ipc_recv", "ipc"],
                "constraints": {},
            },
        )
    )
    b = asyncio.run(
        k.create_process(
            "agent_b",
            capabilities=["file_read", "ipc_send", "ipc_recv", "ipc"],
            intent={
                "goal": "comm",
                "capabilities": ["file_read", "ipc_send", "ipc_recv", "ipc"],
                "constraints": {},
            },
        )
    )
    msg = k.ipc_send(a.id, b.id, "task", {"text": "hello from a"})
    if not msg.get("id"):
        fail(f"ipc_send failed: {msg}")
    recv = k.ipc_recv(b.id, max=5)
    if int(recv.get("count") or 0) < 1:
        fail(f"ipc_recv empty: {recv}")
    ok("IPC send/recv between two agents")

    # deny without cap
    c = asyncio.run(
        k.create_process(
            "agent_c",
            capabilities=["file_read"],
            intent={"goal": "x", "capabilities": ["file_read"], "constraints": {}},
        )
    )
    try:
        k.ipc_send(c.id, b.id, "x", {})
        fail("ipc without cap should deny")
    except Exception:
        ok("IPC deny without capability")

    # services
    svcs = k.service_list()
    names = {s.get("name") for s in (svcs.get("services") or [])}
    if "sys.memory" not in names or "sys.notify" not in names:
        fail(f"builtin services missing: {names}")
    k.sys_memory_put("alice", "pref", {"lang": "zh"})
    g = k.sys_memory_get("alice", "pref")
    if not g.get("found"):
        fail(f"memory get: {g}")
    n = k.sys_notify_push(a.id, "done", "task finished", level="info")
    if not n.get("id"):
        fail(f"notify: {n}")
    ok("Memory + Notify system services")

    # identity cache
    k.identity_cache_put(
        {"id": "id-1", "name": "Bob", "capabilities": ["file_read"], "status": "active"}
    )
    bob = k.identity_cache_get("Bob")
    if not bob or bob.get("id") != "id-1":
        fail(f"identity cache: {bob}")
    ok("Identity hot cache")

    # inbox dual-claim
    item = k.inbox_submit("Bob", "do work", priority=10)
    claim1 = k.inbox_claim("w1", "Bob")
    claim2 = k.inbox_claim("w2", "Bob")
    if not claim1.get("claimed") or claim2.get("claimed"):
        fail(f"double claim: {claim1} {claim2}")
    tok = (claim1.get("item") or {}).get("claim_token")
    done = k.inbox_complete(item["id"], tok, "ok", process_id=a.id)
    if done.get("status") != "done":
        fail(f"complete: {done}")
    ok("Inbox claim atomic (no double dispatch)")

    # skill gate
    pkg = k.skill_register(
        "demo_skill",
        "def run(): return 1",
        version="1.0.0",
        tests=["unit_ok"],
    )
    try:
        k.skill_activate(pkg["id"])
        fail("activate without verify should fail")
    except Exception:
        ok("skill activate blocked pre-verify")
    k.skill_verify(pkg["id"])
    k.skill_activate(pkg["id"])
    if not k.skill_is_loadable("demo_skill"):
        fail("not loadable after activate")
    pkg2 = k.skill_register("demo_skill", "def run(): return 2", version="1.1.0")
    k.skill_verify(pkg2["id"])
    k.skill_activate(pkg2["id"])
    rb = k.skill_rollback("demo_skill")
    if rb.get("content") != "def run(): return 1":
        fail(f"rollback: {rb}")
    pol = k.evolution_policy()
    if pol.get("auto_apply") is not False:
        fail(f"evolution auto_apply must be false: {pol}")
    ok("Skill gate verify/activate/rollback + evolution redline")

    # context VM (min quota floor 64; two ~50-token pages must not both stay resident)
    k.context_set_quota(a.id, 64)
    k.context_put_page(a.id, "p1", "x" * 200)
    k.context_put_page(a.id, "p2", "y" * 200)
    st = k._call("context_status", {"process_id": a.id}) or {}
    res_tok = int(st.get("resident_tokens") or 0)
    if res_tok > 64:
        fail(f"quota not enforced: {st}")
    residents = sum(
        1 for p in (st.get("pages") or []) if p.get("resident")
    )
    if residents > 1:
        fail(f"expected at most 1 resident page under tight quota: {st}")
    ok("Context VM quota / swap")

    # memory layers
    k.memory_layer_put("alice", "working", "important", score=0.9)
    cons = k.memory_layer_consolidate("alice")
    if int(cons.get("promoted_to_episodic") or 0) < 1:
        fail(f"consolidate: {cons}")
    ok("Memory layer consolidate")

    asyncio.run(k.end_process(a.id, state="completed"))
    asyncio.run(k.end_process(b.id, state="completed"))
    asyncio.run(k.end_process(c.id, state="completed"))
    print("=== P1 Integration Smoke PASSED ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
