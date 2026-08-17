"""Collapse dual-authority: extra_roots, mcp_*, session grants, catalog, caps."""

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
        pytest.skip("tevarn-kernel-host missing")
    from backend.kernel_rust.client import RustAgentKernel, reset_rust_kernel_for_tests

    reset_rust_kernel_for_tests()
    kernel = None
    for _ in range(10):
        try:
            kernel = RustAgentKernel(auto_start=True)
            break
        except Exception:
            time.sleep(0.2)
    if kernel is None:
        pytest.skip("host")
    yield kernel
    try:
        kernel._rpc.close()
    except Exception:
        pass
    reset_rust_kernel_for_tests()


def test_catalog_current_time_unique(k) -> None:
    cat = k.tool_catalog() or {}
    pairs = cat.get("tool_to_crew_cap") or []
    tools = [str(p.get("tool")) for p in pairs if isinstance(p, dict)]
    assert tools.count("current_time") == 1
    mapping = {
        str(p["tool"]): str(p["cap"])
        for p in pairs
        if isinstance(p, dict) and p.get("tool") and p.get("cap")
    }
    assert mapping["current_time"] == "current_time"
    assert mapping.get("result_load") == "file_read"
    assert mapping.get("generate_ppt") == "file_rw"


def test_approval_cap_eligible(k) -> None:
    ok = k._call("approval_cap_eligible", {"cap": "web_search", "high_risk_auto": True})
    assert ok.get("eligible") is True
    no = k._call("approval_cap_eligible", {"cap": "sudo", "high_risk_auto": True})
    assert no.get("eligible") is False
    cmd = k._call("approval_cap_eligible", {"cap": "command", "high_risk_auto": False})
    assert cmd.get("eligible") is False


@pytest.mark.asyncio
async def test_mcp_prefix_allow_and_user_deny(k) -> None:
    p = await k.create_process(
        "court_mcp",
        capabilities=["file_read"],
        intent={"goal": "t", "capabilities": ["file_read"], "constraints": {}},
    )
    try:
        k.set_court_policy(
            {
                "permission_enabled": True,
                "workspace_root": str(Path.cwd()),
                "allow_mcp_prefix": True,
                "user_deny": [],
            }
        )
        d = k.decide_tool("mcp_tavily_search", {}, process_id=p.id, emit=False)
        assert d.get("verdict") == "allow"
        assert d.get("matched_rule") == "mcp:mounted_allow"
        k.set_court_policy({"user_deny": ["mcp_tavily_search"]})
        d2 = k.decide_tool("mcp_tavily_search", {}, process_id=p.id, emit=False)
        assert d2.get("verdict") == "deny"
        assert d2.get("layer") == "user_deny"
    finally:
        k.set_court_policy({"user_deny": [], "allow_mcp_prefix": True})
        await k.end_process(p.id, state="completed")


@pytest.mark.asyncio
async def test_extra_roots_not_workspace_deny_and_not_full_allow(k) -> None:
    extra = Path(tempfile.mkdtemp(prefix="tevarn-extra-"))
    target = extra / "notes.md"
    target.write_text("hi", encoding="utf-8")
    ws = Path.cwd()
    p = await k.create_process(
        "court_extra",
        capabilities=["file_read"],
        intent={"goal": "t", "capabilities": ["file_read"], "constraints": {}},
    )
    try:
        k.set_court_policy(
            {
                "permission_enabled": True,
                "workspace_root": str(ws),
                "extra_roots": [str(extra)],
                "allow_mcp_prefix": True,
            }
        )
        d = k.decide_tool(
            "file_read",
            {"path": str(target)},
            process_id=p.id,
            emit=False,
        )
        assert d.get("matched_rule") != "path:workspace"
        assert d.get("matched_rule") != "path:extra_roots"
        d_write = k.decide_tool(
            "file_write",
            {"path": str(target), "content": "x"},
            process_id=p.id,
            emit=False,
        )
        # extra_roots only skip path:workspace; missing write cap still denies
        assert d_write.get("verdict") == "deny"
        assert d_write.get("matched_rule") != "path:extra_roots"
    finally:
        await k.end_process(p.id, state="completed")


@pytest.mark.asyncio
async def test_python_decide_tool_uses_run_extra_roots(k, tmp_path) -> None:
    from backend.kernel.permission_court import decide_tool
    from backend.tools.permissions import run_workspace_context

    extra = tmp_path / "extra"
    extra.mkdir()
    target = extra / "notes.md"
    target.write_text("hi", encoding="utf-8")
    ws = tmp_path / "ws"
    ws.mkdir()
    p = await k.create_process(
        "court_py_extra",
        capabilities=["file_read"],
        intent={"goal": "t", "capabilities": ["file_read"], "constraints": {}},
    )
    try:
        with run_workspace_context(root=str(ws), extra_roots=[str(extra)]):
            d = await decide_tool(
                "file_read",
                {"path": str(target), "_kernel_process_id": p.id},
            )
        assert d.matched_rule != "path:workspace"
        assert d.matched_rule != "path:extra_roots"
        assert d.verdict in ("allow", "ask")
    finally:
        await k.end_process(p.id, state="completed")


@pytest.mark.asyncio
async def test_session_grant_upgrades_ask(k) -> None:
    p = await k.create_process(
        "court_grant",
        capabilities=["file_write", "file_rw"],
        intent={
            "goal": "t",
            "capabilities": ["file_write", "file_rw"],
            "constraints": {"allow_risky": True},
        },
    )
    sid = "court-grant-sess"
    try:
        k._call("session_grant_clear", {"session_id": sid})
        before = k.decide_tool(
            "file_write",
            {"path": "a.txt", "content": "x", "_session_id": sid},
            process_id=p.id,
            emit=False,
        )
        assert before.get("verdict") == "ask"
        k._call("session_grant_add", {"session_id": sid, "sigs": ["file_write"]})
        after = k.decide_tool(
            "file_write",
            {"path": "a.txt", "content": "x", "_session_id": sid},
            process_id=p.id,
            emit=False,
        )
        assert after.get("verdict") == "allow"
        assert after.get("matched_rule") == "session_grant"
        k._call("session_grant_clear", {"session_id": sid})
        cleared = k.decide_tool(
            "file_write",
            {"path": "a.txt", "content": "x", "_session_id": sid},
            process_id=p.id,
            emit=False,
        )
        assert cleared.get("verdict") == "ask"
    finally:
        k._call("session_grant_clear", {"session_id": sid})
        await k.end_process(p.id, state="completed")


def test_python_court_has_no_dual_override() -> None:
    src = (_ROOT / "backend" / "kernel" / "permission_court.py").read_text(
        encoding="utf-8"
    )
    assert "path:extra_roots" not in src
    assert "mcp:override_rust_deny" not in src


def test_file_preview_host_no_dead_8000() -> None:
    src = (
        _ROOT / "frontend" / "components" / "chat" / "FilePreviewHost.tsx"
    ).read_text(encoding="utf-8")
    assert "127.0.0.1:8000" not in src
