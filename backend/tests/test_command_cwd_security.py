"""command heredoc allow + default cwd = workspace."""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.agent.turn_retry import RetryKind, classify_tool_result
from backend.core.config import settings
from backend.services.tools.executors import execute_command
from backend.tools.permissions import resolve_agent_workspace_root


@pytest.fixture(autouse=True)
def _local_execution(monkeypatch):
    """本文件测的是**本机执行**语义（heredoc / 反引号 / 默认 cwd）。

    T5 起沙箱默认开启，而沙箱只允许 cwd 落在其 workspace 根内；
    这些用例用 tmp_path 作 cwd，在沙箱下会被正确拒绝。
    execution mode 现在有三档，测试必须显式声明自己走哪条路径。
    """
    monkeypatch.setattr(settings, "agent_execution_mode", "local", raising=False)
    monkeypatch.setattr(settings, "agent_computer_enabled", False, raising=False)


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
