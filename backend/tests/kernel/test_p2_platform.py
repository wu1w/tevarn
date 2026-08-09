"""P2: coding profile · collab · edit · HAL · wasm · packages · instance."""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault("TEVARN_KERNEL_BACKEND", "rust")
os.environ.setdefault("TEVARN_KERNEL_AUTO_START", "1")


@pytest.fixture(scope="module")
def k():
    from backend.kernel_rust.client import (
        RustAgentKernel,
        is_rust_host_available,
        reset_rust_kernel_for_tests,
        start_kernel_host,
    )

    reset_rust_kernel_for_tests()
    kernel = None
    for _ in range(10):
        try:
            if not is_rust_host_available():
                start_kernel_host()
            kernel = RustAgentKernel(auto_start=True)
            break
        except Exception:
            time.sleep(0.2)
    if kernel is None:
        pytest.skip("host unavailable")
    yield kernel
    try:
        kernel._rpc.close()
    except Exception:
        pass
    reset_rust_kernel_for_tests()


@pytest.mark.asyncio
async def test_coding_profile_and_collab(k) -> None:
    p = await k.create_process(
        "p2t_prof",
        capabilities=["file_read"],
        intent={"goal": "t", "capabilities": ["file_read"], "constraints": {}},
    )
    r = k._call("coding_profile_apply", {"process_id": p.id, "profile": "pair"})
    assert r.get("ok") is True
    k._call("collab_set_plan", {"process_id": p.id, "steps": ["a", "b"]})
    k._call("collab_interrupt", {"process_id": p.id, "reason": "pause"})
    g = k._call("collab_get", {"process_id": p.id})
    assert g.get("interrupted") is True
    k._call("collab_resume", {"process_id": p.id})
    await k.end_process(p.id, state="completed")


@pytest.mark.asyncio
async def test_edit_and_hal(k) -> None:
    p = await k.create_process(
        "p2t_edit",
        capabilities=["file_write", "file_read"],
        intent={
            "goal": "e",
            "capabilities": ["file_write", "file_read"],
            "constraints": {"allow_risky": True},
        },
    )
    with tempfile.TemporaryDirectory() as td:
        path = str(Path(td) / "x.txt")
        Path(path).write_text("a\n", encoding="utf-8")
        s = k._call(
            "edit_propose",
            {"process_id": p.id, "path": path, "after": "a\nb\n"},
        )
        assert s.get("status") == "proposed"
        k._call("edit_confirm", {"session_id": s["id"]})
        assert Path(path).read_text(encoding="utf-8") == "a\nb\n"
        k._call("edit_rollback", {"session_id": s["id"]})
        assert Path(path).read_text(encoding="utf-8") == "a\n"
    plat = k._call("hal_platform")
    assert plat.get("os")
    await k.end_process(p.id, state="completed")


@pytest.mark.asyncio
async def test_wasm_pkg_instance(k) -> None:
    # Real wasmtime path (WAT)
    wat = """(module
      (func (export "main") (result i32)
        i32.const 1
        i32.const 1
        i32.add)
    )"""
    m = k._call(
        "wasm_load",
        {"name": "m", "content": wat, "fuel_limit": 20000},
    )
    assert m.get("id")
    assert m.get("wasmtime_ready") is True
    k._call("wasm_activate", {"module_id": m["id"]})
    inv = k._call(
        "wasm_invoke",
        {
            "module_id": m["id"],
            "entry": "main",
            "params": {},
        },
    )
    assert inv.get("ok") is True
    assert inv.get("engine") == "wasmtime"
    assert (inv.get("output") or {}).get("return") == 2

    k._call("pkg_set_signing_key", {"key": "p2-test-signing-key-32bytes!!"})
    body = "ok"
    sig = k._call("pkg_sign", {"content": body})["signature"]
    pkg = k._call(
        "pkg_install",
        {
            "name": "p2pkg",
            "version": "1.0",
            "content": body,
            "signature": sig,
            "permissions": ["file_read"],
        },
    )
    assert pkg.get("status") == "verified"

    exp = k._call("instance_export", {"identity": "u1"})
    assert exp.get("content_hash")
    imp = k._call("instance_import", {"bundle": exp})
    assert imp.get("identity") == "u1"
    assert imp.get("hydrated", {}).get("identity_cache") is True

    compat = k._call("abi_compat")
    assert compat.get("min_compatible_abi") == "1.0.0"
    assert compat.get("abi_break_count") == 0
    neg = k._call("abi_negotiate", {"client_abi": "1.0.0"})
    assert neg.get("compatible") is True

    # E-01 spawn + E-04 explain + E-06 require_secure flag
    sp = k._call(
        "coding_profile_spawn",
        {"identity": "p2_spawn", "profile": "pair"},
    )
    assert sp.get("ok") is True
    assert (sp.get("process") or {}).get("id")
    expl = k._call("wasm_explain", {})
    assert (expl.get("limits") or {}).get("fuel") or expl.get("status")
    sec = k._call("pkg_set_require_secure", {"require": False})
    assert sec.get("ok") is True
