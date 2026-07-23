"""工作区根解析与路径安全。"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from backend.tools.permissions import (
    ToolPermissionManager,
    detect_project_root,
    resolve_agent_workspace_root,
)
from backend.services.tools.executors import _resolve_workspace_path, execute_file_read


def test_detect_project_root_finds_takton():
    root = detect_project_root(str(Path(__file__).resolve()))
    assert (Path(root) / "backend").is_dir()
    assert (Path(root) / "backend" / "agent" / "tool_policy.py").is_file()


def test_default_workspace_is_project_root(monkeypatch):
    monkeypatch.delenv("TAKTON_FILE_BROWSER_ROOT", raising=False)
    # force settings default
    from backend.core import config as cfg

    monkeypatch.setattr(cfg.settings, "file_browser_root", ".", raising=False)
    root = resolve_agent_workspace_root()
    assert (Path(root) / "backend").is_dir()
    mgr = ToolPermissionManager()
    assert Path(mgr.workspace_root) == Path(root)
    # backend file allowed
    rel = "backend/agent/tool_policy.py"
    assert mgr.is_path_allowed(rel)
    abs_p = str((Path(root) / rel).resolve())
    assert mgr.is_path_allowed(abs_p)


def test_sandbox_workspace_env(monkeypatch, tmp_path):
    monkeypatch.setenv("TAKTON_FILE_BROWSER_ROOT", str(tmp_path))
    root = resolve_agent_workspace_root()
    assert Path(root) == tmp_path.resolve()
    mgr = ToolPermissionManager()
    assert not mgr.is_path_allowed(str(Path(__file__).resolve()))


def test_resolve_abs_and_rel():
    base = detect_project_root()
    rel_full, b1 = _resolve_workspace_path(base, "backend/agent/tool_policy.py")
    assert rel_full.endswith("tool_policy.py")
    abs_target = str(Path(base) / "backend/agent/tool_policy.py")
    abs_full, b2 = _resolve_workspace_path(base, abs_target)
    assert Path(abs_full).resolve() == Path(abs_target).resolve()


@pytest.mark.asyncio
async def test_file_read_backend_policy():
    root = detect_project_root()
    out = await execute_file_read(
        {"base_path": root},
        {"filepath": "backend/agent/tool_policy.py"},
    )
    assert "DEFAULT_CHAT_TOOL_WHITELIST" in out
    assert not out.startswith("[Error]")
    assert not out.startswith("[Security")
