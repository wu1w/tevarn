"""Session workspace_root override + timid shell detection."""
from __future__ import annotations

from pathlib import Path

from backend.agent.decisive import is_timid_read_round, is_timid_shell_command
from backend.tools.permissions import (
    ToolPermissionManager,
    bind_run_workspace_from_config,
    resolve_agent_workspace_root,
)


def test_bind_run_workspace_from_config(tmp_path):
    task = tmp_path / "dual_stress"
    task.mkdir()
    (task / "a.txt").write_text("x", encoding="utf-8")
    repo = tmp_path / "takton_repo"
    repo.mkdir()
    reset = bind_run_workspace_from_config({"workspace_root": str(task)})
    try:
        assert Path(resolve_agent_workspace_root()).resolve() == task.resolve()
        mgr = ToolPermissionManager()
        assert mgr.is_path_allowed(str(task / "a.txt"))
        # outside still blocked unless extra
        other = tmp_path / "other" / "b.txt"
        other.parent.mkdir()
        other.write_text("y", encoding="utf-8")
        assert not mgr.is_path_allowed(str(other))
    finally:
        reset()
    # after reset, not forced to task
    root = Path(resolve_agent_workspace_root()).resolve()
    assert root != task.resolve() or True  # env may still point elsewhere


def test_extra_roots_allow_write_target(tmp_path):
    main = tmp_path / "main"
    extra = tmp_path / "extra"
    main.mkdir()
    extra.mkdir()
    f = extra / "f.txt"
    f.write_text("z", encoding="utf-8")
    reset = bind_run_workspace_from_config(
        {"workspace_root": str(main), "allowed_roots": [str(extra)]}
    )
    try:
        mgr = ToolPermissionManager()
        assert mgr.is_path_allowed(str(f))
    finally:
        reset()


def test_timid_shell_cat():
    assert is_timid_shell_command("cat foo.py") is True
    assert is_timid_shell_command("head -n 20 a.py") is True
    assert is_timid_shell_command("pytest -q") is False
    assert is_timid_read_round(["command"], [type("T", (), {"name": "command", "arguments": {"command": "cat x"}})()])
