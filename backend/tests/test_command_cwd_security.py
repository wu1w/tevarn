"""command heredoc allow + default cwd = workspace."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from backend.services.tools.executors import execute_command
from backend.tools.permissions import resolve_agent_workspace_root
from backend.agent.turn_retry import classify_tool_result, RetryKind


@pytest.mark.asyncio
async def test_heredoc_not_blocked(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKTON_FILE_BROWSER_ROOT", str(tmp_path))
    target = tmp_path / "out.txt"
    cmd = "cat > out.txt <<'EOF'\nhello_heredoc\nEOF\n"
    # should not return Security Blocked for newlines
    res = await execute_command({"base_path": str(tmp_path)}, {"command": cmd, "cwd": str(tmp_path)})
    assert "Security Blocked" not in res, res
    assert target.read_text(encoding="utf-8") == "hello_heredoc\n"


@pytest.mark.asyncio
async def test_backtick_not_blocked_simple(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKTON_FILE_BROWSER_ROOT", str(tmp_path))
    res = await execute_command(
        {"base_path": str(tmp_path)},
        {"command": "echo `echo hi_backtick`", "cwd": str(tmp_path)},
    )
    assert "Security Blocked" not in res, res
    assert "hi_backtick" in res


@pytest.mark.asyncio
async def test_default_cwd_is_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKTON_FILE_BROWSER_ROOT", str(tmp_path))
    (tmp_path / "marker").write_text("ok", encoding="utf-8")
    res = await execute_command({}, {"command": "pwd && test -f marker && echo MARK_OK"})
    assert "Security Blocked" not in res
    assert "MARK_OK" in res
    # pwd should be workspace
    root = resolve_agent_workspace_root()
    assert str(Path(root).resolve()) in res.replace("\r", "")


def test_classify_security_and_127():
    assert classify_tool_result("[Security Blocked] x") == RetryKind.TOOL_TRANSIENT
    assert classify_tool_result("[Exit 127] cwd=/tmp") == RetryKind.TOOL_TRANSIENT
