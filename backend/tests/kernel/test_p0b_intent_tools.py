"""P0-B: Intent → capabilities → tool schema filter (Rust host)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault("TEVARN_KERNEL_BACKEND", "rust")
os.environ.setdefault("TEVARN_KERNEL_AUTO_START", "1")
os.environ.setdefault("TEVARN_KERNEL_REQUIRE_INTENT", "true")


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
    kernel = RustAgentKernel(auto_start=True)
    yield kernel
    try:
        kernel._rpc.close()
    except Exception:
        pass
    reset_rust_kernel_for_tests()


@pytest.mark.asyncio
async def test_create_without_caps_gets_readonly_intent(k) -> None:
    from backend.kernel_rust.client import KernelPermissionError

    p = await k.create_process("p0b_main", session_id="s1")
    # require_intent default → grantable caps, not None
    assert p.capabilities is not None
    assert "file_read" in (p.capabilities or [])
    assert "terminal" not in (p.capabilities or [])
    d = await k.mediate(p.id, "tool_call", "file_read")
    assert d.allowed
    with pytest.raises(KernelPermissionError):
        await k.mediate(p.id, "tool_call", "terminal")
    await k.end_process(p.id, state="completed")


@pytest.mark.asyncio
async def test_explicit_intent_drops_risky(k) -> None:
    intent = {
        "goal": "read only summary",
        "capabilities": ["file_read", "grep", "terminal"],
        "constraints": {"allow_risky": False},
    }
    p = await k.create_process("p0b_intent", intent=intent)
    assert "file_read" in (p.capabilities or [])
    assert "terminal" not in (p.capabilities or [])
    meta = p.meta or {}
    dropped = meta.get("intent_dropped") or []
    assert "terminal" in dropped
    tools = k.filter_tools(
        p.id, ["file_read", "grep", "terminal", "file_write", "glob"]
    )
    assert "file_read" in tools and "grep" in tools
    assert "terminal" not in tools
    await k.end_process(p.id, state="completed")


@pytest.mark.asyncio
async def test_apply_intent_rpc(k) -> None:
    p = await k.create_process(
        "p0b_apply",
        capabilities=["file_read"],
        intent={
            "goal": "start",
            "capabilities": ["file_read"],
            "constraints": {},
        },
    )
    tok, dropped = k.apply_intent(
        p.id,
        {
            "goal": "expand",
            "capabilities": ["file_read", "grep", "terminal"],
            "constraints": {"allow_risky": False},
        },
    )
    assert tok.allows("grep")
    assert "terminal" in dropped
    fresh = k.get_process(p.id)
    assert fresh and "grep" in (fresh.capabilities or [])
    await k.end_process(p.id, state="completed")


def test_tool_catalog_rpc(k) -> None:
    cat = k.tool_catalog()
    assert cat.get("version") == 1
    pairs = cat.get("tool_to_crew_cap") or []
    assert any(p.get("tool") == "file_read" and p.get("cap") == "file_rw" for p in pairs)
