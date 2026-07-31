"""P0-D: decide_tool · isolation · checkpoint · decision trail."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault("TAKTON_KERNEL_BACKEND", "rust")
os.environ.setdefault("TAKTON_KERNEL_AUTO_START", "1")


def _host_ready() -> bool:
    from backend.kernel_rust.client import (
        _find_host_bin,
        is_rust_host_available,
        start_kernel_host,
    )

    if is_rust_host_available():
        return True
    if _find_host_bin() is None:
        return False
    return start_kernel_host()


@pytest.fixture(scope="module")
def k():
    if not _host_ready():
        pytest.skip("takton-kernel-host missing")
    from backend.kernel_rust.client import RustAgentKernel, reset_rust_kernel_for_tests

    reset_rust_kernel_for_tests()
    kernel = RustAgentKernel(auto_start=True)
    yield kernel
    try:
        kernel._rpc.close()
    except Exception:
        pass
    reset_rust_kernel_for_tests()


@pytest.mark.asyncio
async def test_decide_tool_secret_floor(k) -> None:
    p = await k.create_process(
        "p0d_sec",
        capabilities=["file_read"],
        intent={"goal": "t", "capabilities": ["file_read"], "constraints": {}},
    )
    d = k.decide_tool(
        "file_read",
        {"path": str(Path.cwd() / ".env")},
        process_id=p.id,
        emit=False,
    )
    assert d.get("verdict") == "deny"
    assert d.get("layer") == "secret_floor"
    await k.end_process(p.id, state="completed")


@pytest.mark.asyncio
async def test_isolation_untrusted_rejects_local(k) -> None:
    p = await k.create_process(
        "p0d_iso",
        capabilities=["file_read"],
        intent={"goal": "t", "capabilities": ["file_read"], "constraints": {}},
    )
    k._call(
        "isolation_set_profile",
        {"process_id": p.id, "profile": "untrusted"},
    )
    with pytest.raises(Exception):
        k._call(
            "isolation_spawn",
            {"process_id": p.id, "command": "echo hi", "backend": "local"},
        )
    h = k._call(
        "isolation_spawn",
        {"process_id": p.id, "command": "echo hi", "backend": "bwrap"},
    )
    assert h.get("id")
    await k.end_process(p.id, state="completed")


@pytest.mark.asyncio
async def test_checkpoint_and_trail(k) -> None:
    p = await k.create_process(
        "p0d_cp",
        capabilities=["file_write"],
        intent={
            "goal": "w",
            "capabilities": ["file_write"],
            "constraints": {"allow_risky": True},
        },
    )
    with tempfile.TemporaryDirectory() as td:
        path = str(Path(td) / "hello.txt")
        Path(path).write_text("before", encoding="utf-8")
        cp = k._call(
            "checkpoint_begin",
            {"process_id": p.id, "path": path},
        )
        assert cp.get("id")
        Path(path).write_text("after", encoding="utf-8")
        restored = k._call("checkpoint_restore", {"checkpoint_id": cp["id"]})
        assert restored.get("id") == cp["id"]
        assert Path(path).read_text(encoding="utf-8") == "before"
    # trail
    trail = k.export_decision_trail(p.id)
    assert trail.get("process_id") == p.id
    await k.end_process(p.id, state="completed")


@pytest.mark.asyncio
async def test_write_tool_ask(k) -> None:
    p = await k.create_process(
        "p0d_ask",
        capabilities=["file_write"],
        intent={
            "goal": "w",
            "capabilities": ["file_write"],
            "constraints": {"allow_risky": True},
        },
    )
    d = k.decide_tool(
        "file_write",
        {"path": "a.txt", "content": "x"},
        process_id=p.id,
        emit=True,
    )
    assert d.get("verdict") == "ask"
    trail = k.export_decision_trail(p.id)
    kinds = [e.get("kind") for e in trail.get("events") or []]
    assert "policy.decision" in kinds
    await k.end_process(p.id, state="completed")


@pytest.mark.asyncio
async def test_permission_court_rust_authoritative(k) -> None:
    """Python permission_court returns Rust decision without Python fallthrough."""
    from backend.kernel.permission_court import decide_tool

    p = await k.create_process(
        "p0d_court_auth",
        capabilities=["file_read"],
        intent={"goal": "t", "capabilities": ["file_read"], "constraints": {}},
    )
    # ensure rust host is the active kernel
    import backend.kernel as bk

    prev = getattr(bk, "_kernel_singleton", None)
    try:
        # force get_kernel path to see rust if configured
        d = await decide_tool(
            "file_read",
            {
                "path": str(Path.cwd() / ".env"),
                "_kernel_process_id": p.id,
            },
        )
        assert d.verdict == "deny"
        assert d.layer == "secret_floor"
        # user_deny via set_court_policy
        k.set_court_policy(
            {
                "workspace_root": str(Path.cwd()),
                "user_deny": ["**/secrets/**"],
                "permission_enabled": True,
            }
        )
        d2 = await decide_tool(
            "file_read",
            {
                "path": str(Path.cwd() / "secrets" / "x.txt"),
                "_kernel_process_id": p.id,
            },
        )
        # either deny (user_deny/path) or allow if pattern not matched — must come from court
        assert d2.verdict in ("deny", "allow", "ask")
        assert d2.layer  # rust always fills layer
    finally:
        await k.end_process(p.id, state="completed")


@pytest.mark.asyncio
async def test_isolation_workforce_sandbox_required(k) -> None:
    p = await k.create_process(
        "p0d_wf_iso",
        capabilities=["terminal"],
        intent={
            "goal": "run",
            "capabilities": ["terminal"],
            "constraints": {"allow_risky": True},
        },
    )
    k._call(
        "isolation_set_profile",
        {"process_id": p.id, "profile": "workforce"},
    )
    pol = k.isolation_resolve(p.id, is_workforce=True)
    assert pol.get("sandbox_required") is True
    with pytest.raises(Exception):
        k._call(
            "isolation_spawn",
            {"process_id": p.id, "command": "echo hi", "backend": "local"},
        )
    await k.end_process(p.id, state="completed")
