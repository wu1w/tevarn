# -*- coding: utf-8 -*-
"""0.6 P0 acceptance-oriented tests (no host required unless noted)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


# ── K-03 cap filter after pack expand helper ─────────────────


def test_cap_tools_trims_by_local_caps():
    from backend.agent.cap_tools import filter_tools_for_process

    tools = [
        {"type": "function", "function": {"name": "file_read", "parameters": {}}},
        {"type": "function", "function": {"name": "command", "parameters": {}}},
        {"type": "function", "function": {"name": "file_write", "parameters": {}}},
    ]
    proc = SimpleNamespace(
        id="p1",
        capabilities=frozenset({"file_read", "grep"}),
    )

    class K:
        def filter_tools(self, pid, names):
            return [n for n in names if n in ("file_read", "grep")]

    import backend.agent.cap_tools as ct

    monkey = pytest.MonkeyPatch()
    monkey.setattr("backend.kernel.get_kernel", lambda: K())
    try:
        out = filter_tools_for_process(tools, proc)
        names = [(t.get("function") or {}).get("name") for t in out]
        assert names == ["file_read"]
        assert "command" not in names
    finally:
        monkey.undo()


def test_cap_tools_fail_closed_on_filter_error():
    from backend.agent.cap_tools import filter_tools_for_process

    tools = [{"type": "function", "function": {"name": "file_read", "parameters": {}}}]
    proc = SimpleNamespace(id="p1", capabilities=frozenset({"file_read"}))

    class Boom:
        def filter_tools(self, *a, **k):
            raise RuntimeError("host down mid-filter")

    monkey = pytest.MonkeyPatch()
    monkey.setattr("backend.kernel.get_kernel", lambda: Boom())
    try:
        out = filter_tools_for_process(tools, proc, fail_closed_on_error=True)
        assert out == []
    finally:
        monkey.undo()


# ── Criterion 1: bypass inventory (static) ───────────────────


def test_tool_execute_paths_call_enforce_tool_gate():
    """Static inventory: production execute entrypoints reference enforce_tool_gate."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    must_contain = [
        root / "tools" / "registry.py",
        root / "agent" / "loop_tools.py",
        root / "kernel" / "tool_gate.py",
    ]
    for p in must_contain:
        text = p.read_text(encoding="utf-8")
        assert "enforce_tool_gate" in text or "mediate_tool_call" in text, p.name


# ── K-04 dual priority unit (run_gate pick) ──────────────────


def test_scheduler_priority_foreground_before_background():
    """K-04：低 priority 数值 = 更高紧迫度；前台应先于后台出队。"""
    from backend.kernel.scheduler import AgentScheduler

    sch = AgentScheduler()
    sch.submit(process_id="bg", priority=20)
    sch.submit(process_id="fg", priority=5)
    nxt = sch.next()
    assert nxt is not None
    assert nxt.process_id == "fg"
    nxt2 = sch.next()
    assert nxt2 is not None
    assert nxt2.process_id == "bg"


# ── K-05 charge_for_tool memory hard stop ────────────────────


def test_charge_for_tool_memory_over_limit(monkeypatch):
    from backend.kernel import tool_gate as tg

    class K:
        def resource_usage(self, pid):
            return {"memory_bytes": {"used": 300, "limit": 100}}

        def resource_charge(self, *a, **k):
            raise AssertionError("should not charge when memory over")

    monkeypatch.setattr("backend.kernel.get_kernel", lambda: K())
    with pytest.raises(RuntimeError, match="memory_bytes"):
        tg.charge_for_tool("file_read", "p1", {})


# ── tools API requires process ───────────────────────────────


@pytest.mark.asyncio
async def test_tools_api_execute_requires_process(monkeypatch):
    from backend.api.routes import tools as tools_route
    from backend.schemas.tool import ToolExecuteRequest
    from fastapi import HTTPException

    tool = SimpleNamespace(id="t1", name="file_read")
    repo = AsyncMock()
    repo.get_by_id = AsyncMock(return_value=tool)
    user = SimpleNamespace(id="u1")
    monkeypatch.delenv("TAKTON_ALLOW_DEBUG_TOOL_EXECUTE", raising=False)

    with pytest.raises(HTTPException) as ei:
        await tools_route.execute_tool_endpoint(
            tool_id=tool.id,
            req=ToolExecuteRequest(arguments={"path": "x"}),
            current_user=user,
            repo=repo,
        )
    assert ei.value.status_code == 403


# ── K-06 workforce sandbox message still present ────────────


def test_workforce_sandbox_fail_closed_message():
    from backend.kernel.tool_gate import workforce_sandbox_fail_message

    msg = workforce_sandbox_fail_message(profile_id="workforce")
    assert "workforce" in msg.lower() or "沙箱" in msg or "sandbox" in msg.lower()
