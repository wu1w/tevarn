# -*- coding: utf-8 -*-
"""H-03 / H-06 closeout: HTTP forge strip + MCP rebuild fail-closed + OOTB run_gate."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_http_execute_forged_dual_flags_no_pid_403(monkeypatch):
    """Forged _tool_gate_passed+_tool_gate_internal without pid still 403."""
    from backend.api.routes import tools as tools_route
    from backend.schemas.tool import ToolExecuteRequest
    from fastapi import HTTPException

    tool = SimpleNamespace(id="t1", name="file_read")
    repo = AsyncMock()
    repo.get_by_id = AsyncMock(return_value=tool)
    user = SimpleNamespace(id="u1")
    monkeypatch.delenv("TEVARN_ALLOW_DEBUG_TOOL_EXECUTE", raising=False)

    with pytest.raises(HTTPException) as ei:
        await tools_route.execute_tool_endpoint(
            tool_id=tool.id,
            req=ToolExecuteRequest(
                arguments={
                    "_tool_gate_passed": True,
                    "_tool_gate_internal": True,
                    "path": "x",
                }
            ),
            current_user=user,
            repo=repo,
        )
    assert ei.value.status_code == 403


def test_mcp_rebuild_empty_replaces_previous_list():
    """Empty MCP rebuild must assign (fail-closed), not keep the previous wider list."""
    root = Path(__file__).resolve().parents[2]
    loop_src = (root / "agent" / "loop.py").read_text(encoding="utf-8")
    assert "keeping previous list" not in loop_src
    assert "tools = rebuilt" in loop_src
    assert "fail-closed empty schema" in loop_src


def test_http_execute_pops_forged_gate_flags():
    root = Path(__file__).resolve().parents[2]
    src = (root / "api" / "routes" / "tools.py").read_text(encoding="utf-8")
    assert 'args.pop("_tool_gate_passed", None)' in src
    assert 'args.pop("_tool_gate_internal", None)' in src
    assert "enforce_tool_gate" in src


def test_pack_refilter_fail_closed_empty_schema():
    root = Path(__file__).resolve().parents[2]
    src = (root / "agent" / "phases" / "tool_round.py").read_text(encoding="utf-8")
    assert "state.tools = []" in src
    assert "cap re-filter after pack expand failed" in src


def test_run_gate_soft_skips_without_call():
    """OOTB: Python kernel without _call must not brick first chat."""
    root = Path(__file__).resolve().parents[2]
    src = (root / "agent" / "loop.py").read_text(encoding="utf-8")
    assert "run_gate skipped: kernel has no _call" in src
    assert 'raise RuntimeError(\n                    "run_gate required but kernel has no _call' not in src


def test_tool_gate_mediates_mcp_as_manage_mcp_source():
    root = Path(__file__).resolve().parents[2]
    src = (root / "kernel" / "tool_gate.py").read_text(encoding="utf-8")
    assert 'mediate_target = "manage_mcp"' in src
    assert "不经 kernel mediate" not in src
