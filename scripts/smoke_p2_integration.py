#!/usr/bin/env python3
"""P2 end-to-end smoke: coding profile · collab · edit · HAL · wasm · pkg · instance."""

from __future__ import annotations

import os
import sys
import tempfile
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
        RustAgentKernel,
        is_rust_host_available,
        reset_rust_kernel_for_tests,
        start_kernel_host,
    )

    print("=== P2 Integration Smoke ===")
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
        fail("cannot connect host")

    methods = set(k.list_methods())
    need = {
        "coding_profile_apply",
        "collab_interrupt",
        "edit_propose",
        "edit_confirm",
        "repo_index_build",
        "hal_resolve_path",
        "wasm_load",
        "pkg_install",
        "instance_export",
        "abi_compat",
    }
    missing = need - methods
    if missing:
        fail(f"ABI missing P2 methods: {missing}")
    ok(f"ABI P2 methods present total={len(methods)}")

    compat = k._call("abi_compat") or {}
    if compat.get("abi") != "1.0.0":
        fail(f"abi_compat: {compat}")
    ok(f"ABI compat window: {compat.get('compat_window')}")

    import asyncio

    p = asyncio.run(
        k.create_process(
            "p2_coder",
            capabilities=["file_read"],
            intent={"goal": "start", "capabilities": ["file_read"], "constraints": {}},
        )
    )
    # H1 coding profile
    applied = k._call(
        "coding_profile_apply",
        {"process_id": p.id, "profile": "engineering"},
    ) or {}
    if not applied.get("ok"):
        fail(f"coding_profile_apply: {applied}")
    ok(f"coding profile engineering tools={len(applied.get('tools') or [])}")

    # H2 collab
    k._call("collab_set_plan", {"process_id": p.id, "steps": ["read", "edit", "test"]})
    k._call("collab_interrupt", {"process_id": p.id, "reason": "user rethink"})
    k._call("collab_revise_plan", {"process_id": p.id, "steps": ["read", "patch"]})
    k._call("collab_resume", {"process_id": p.id})
    appr = k._call(
        "collab_request_approval",
        {
            "process_id": p.id,
            "kind": "write",
            "summary": "apply patch",
            "detail": {},
        },
    ) or {}
    k._call(
        "collab_resolve_approval",
        {
            "process_id": p.id,
            "approval_id": appr.get("id"),
            "approve": True,
        },
    )
    ok("collab interrupt / revise plan / approve")

    # H3 edit confirm/rollback
    with tempfile.TemporaryDirectory() as td:
        fpath = str(Path(td) / "demo.txt")
        Path(fpath).write_text("line1\n", encoding="utf-8")
        sess = k._call(
            "edit_propose",
            {
                "process_id": p.id,
                "path": fpath,
                "after": "line1\nline2\n",
            },
        ) or {}
        if not sess.get("id") or "diff" not in sess:
            fail(f"edit_propose: {sess}")
        k._call("edit_confirm", {"session_id": sess["id"]})
        if Path(fpath).read_text(encoding="utf-8") != "line1\nline2\n":
            fail("edit confirm write failed")
        k._call("edit_rollback", {"session_id": sess["id"]})
        if Path(fpath).read_text(encoding="utf-8") != "line1\n":
            fail("edit rollback failed")
        ok("edit propose/confirm/rollback")

        # H4 repo index
        idx = k._call(
            "repo_index_build",
            {"process_id": p.id, "root": td, "max_depth": 3},
        ) or {}
        if int(idx.get("total_files") or 0) < 1:
            fail(f"repo_index: {idx}")
        ok(f"repo index files={idx.get('total_files')}")

    # I2 HAL
    plat = k._call("hal_platform") or {}
    if not plat.get("os"):
        fail(f"hal_platform: {plat}")
    path_r = k._call("hal_resolve_path", {"path": ".", "workspace": str(ROOT)}) or {}
    if not path_r.get("resolved"):
        fail(f"hal path: {path_r}")
    cmd = k._call("hal_resolve_command", {"logical": "python", "args": []}) or {}
    if not cmd.get("program"):
        fail(f"hal cmd: {cmd}")
    ok(f"HAL os={plat.get('os')} python={cmd.get('program')}")

    # I1 WASM — real wasmtime Cranelift (WAT → machine code)
    wat = """(module
      (func (export "main") (result i32)
        i32.const 7
        i32.const 35
        i32.add)
    )"""
    mod = k._call(
        "wasm_load",
        {"name": "demo", "content": wat, "fuel_limit": 50000},
    ) or {}
    if not mod.get("id"):
        fail(f"wasm_load: {mod}")
    if not mod.get("wasmtime_ready"):
        fail(f"expected wasmtime_ready: {mod}")
    k._call("wasm_activate", {"module_id": mod["id"]})
    inv = k._call(
        "wasm_invoke",
        {
            "module_id": mod["id"],
            "entry": "main",
            "params": {},
        },
    ) or {}
    if not inv.get("ok"):
        fail(f"wasm_invoke wasmtime: {inv}")
    if inv.get("engine") != "wasmtime":
        fail(f"expected engine=wasmtime got {inv.get('engine')}")
    if (inv.get("output") or {}).get("return") != 42:
        fail(f"wasm return expected 42: {inv}")
    ok(f"wasmtime return=42 fuel_used={inv.get('fuel_used')}")

    # hostcall ledger fallback still works for invalid/fake modules + ops harness
    fake = (bytes([0x00, ord("a"), ord("s"), ord("m"), 1, 0, 0, 0]) + b"\x00" * 32).decode(
        "latin-1"
    )
    mod2 = k._call(
        "wasm_load",
        {"name": "fake", "content": fake, "fuel_limit": 5000},
    ) or {}
    k._call("wasm_activate", {"module_id": mod2["id"]})
    inv2 = k._call(
        "wasm_invoke",
        {
            "module_id": mod2["id"],
            "entry": "main",
            "params": {
                "engine": "hostcall",
                "ops": [{"op": "log", "msg": "hi"}],
            },
        },
    ) or {}
    if not inv2.get("ok"):
        fail(f"wasm hostcall fallback: {inv2}")
    ok(f"wasm hostcall_ledger fallback ok engine={inv2.get('engine')}")

    # I3 package manager — set a strong signing key (insecure_default never verifies)
    k._call(
        "pkg_set_signing_key",
        {"key": "smoke-test-pkg-signing-key-32b!!"},
    )
    body = "print('pkg')"
    sig = (k._call("pkg_sign", {"content": body}) or {}).get("signature")
    pkg = k._call(
        "pkg_install",
        {
            "name": "demo_pkg",
            "version": "1.0.0",
            "content": body,
            "signature": sig,
            "permissions": ["file_read"],
        },
    ) or {}
    if pkg.get("status") not in ("verified", "installed", "active"):
        fail(f"pkg_install: {pkg}")
    k._call("pkg_activate", {"name": "demo_pkg"})
    bad = k._call(
        "pkg_install",
        {
            "name": "evil",
            "version": "0.1",
            "content": "api_key=sk-abcdefghijklmnop",
            "signature": (k._call("pkg_sign", {"content": "api_key=sk-abcdefghijklmnop"}) or {}).get(
                "signature"
            ),
        },
    ) or {}
    if bad.get("status") != "quarantined":
        fail(f"expected quarantine: {bad}")
    ok("package install/sign/quarantine")

    # I4 instance export/import
    exp = k._call("instance_export", {"identity": "coder", "process_id": p.id}) or {}
    if not exp.get("id") or not exp.get("content_hash"):
        fail(f"instance_export: {exp}")
    imp = k._call("instance_import", {"bundle": exp}) or {}
    if imp.get("identity") != "coder":
        fail(f"instance_import: {imp}")
    ok("instance export/import")

    profiles = k._call("coding_profile_list") or {}
    if len(profiles.get("profiles") or []) < 2:
        fail(f"profiles: {profiles}")
    ok("coding profiles listed")

    asyncio.run(k.end_process(p.id, state="completed"))
    print("=== P2 Integration Smoke PASSED ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
