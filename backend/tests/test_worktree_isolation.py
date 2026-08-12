"""Worktree + parallel agent isolation unit tests."""
from __future__ import annotations

import inspect
from pathlib import Path


def test_run_subagent_accepts_workspace_root():
    from backend.agent.subagent_runner import run_subagent

    sig = inspect.signature(run_subagent)
    assert "workspace_root" in sig.parameters
    assert "worktree_name" in sig.parameters


def test_worktree_module_api():
    from backend.project.worktree import add_worktree, find_git_root, list_worktrees, worktrees_base

    assert callable(add_worktree)
    assert callable(find_git_root)
    assert callable(list_worktrees)
    assert callable(worktrees_base)


def test_implement_type_requests_worktree():
    from backend.agent.subagent_types import resolve_type

    spec = resolve_type("implement")
    assert spec.worktree is True


def test_bind_workspace_from_config(tmp_path, monkeypatch):
    from backend.tools.permissions import bind_run_workspace_from_config, resolve_agent_workspace_root

    cfg = {"workspace_root": str(tmp_path), "worktree_isolated": True}
    reset = bind_run_workspace_from_config(cfg)
    try:
        root = resolve_agent_workspace_root()
        assert Path(root).resolve() == tmp_path.resolve()
    finally:
        if callable(reset):
            reset()
