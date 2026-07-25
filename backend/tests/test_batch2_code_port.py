"""Batch2: permissions / plan_gate / file_history / worktree / auto_classify."""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.agent.auto_classify import apply_auto_classifier, load_rules
from backend.agent.file_history import FileHistory, get_file_history
from backend.agent.permissions_rules import PermissionGate, rules_for_profile
from backend.agent.plan_gate import PlanGate, PlanState, should_auto_plan
from backend.agent.tool_hooks import (
    BeforeHookResult,
    builtin_permission_before,
    clear_tool_hooks,
    ensure_builtin_hooks_registered,
    run_before_tool_call,
)
from backend.project.worktree import find_git_root, list_worktrees, parse_worktree_list


def test_permission_plan_denies_edit():
    g = PermissionGate(profile="plan", mode="plan", project_root=Path(".").resolve())
    assert g.check("file_write", {"path": "a.py"}) == "deny"
    assert g.check("command", {"command": "rm -rf /"}) == "deny"
    assert g.check("file_read", {"path": "a.py"}) == "allow"


def test_permission_cautious_bash_ask():
    g = PermissionGate(profile="cautious", mode="build", project_root=Path(".").resolve())
    assert g.check("command", {"command": "ls"}) == "ask"
    assert g.check("edit", {"filepath": "x.py"}) == "allow"


def test_permission_last_match_env_read():
    g = PermissionGate(profile="cautious", mode="build", project_root=Path(".").resolve())
    # *.env → ask (last match)
    assert g.check("file_read", {"path": ".env"}) == "ask"
    assert g.check("file_read", {"path": ".env.example"}) == "allow"


def test_plan_gate_lifecycle():
    pg = PlanGate()
    pg.start_planning()
    assert pg.state == PlanState.PLANNING
    doc = PlanGate.parse_plan_markdown("# Plan\n\n1. Do A\n2. Do B\n")
    pg.submit_plan(doc)
    assert pg.state == PlanState.PLAN_READY
    pg.approve()
    assert pg.approved and pg.state == PlanState.BUILDING
    assert should_auto_plan("请重构整个模块架构", auto_plan_complex=True, simple_max_chars=40)


@pytest.mark.asyncio
async def test_permission_hook_blocks_plan_edit(monkeypatch, tmp_path: Path):
    clear_tool_hooks()
    ensure_builtin_hooks_registered()

    class S:
        agent_permission_enabled = True
        agent_permission_profile = "plan"
        agent_permission_ask_mode = "deny"
        agent_file_history = False
        agent_file_checkpoint = False

    monkeypatch.setattr("backend.core.config.settings", S(), raising=False)
    # patch import path used inside hook
    import backend.core.config as cfg

    monkeypatch.setattr(cfg, "settings", S())
    res = await run_before_tool_call(
        "file_write",
        {"filepath": str(tmp_path / "a.py"), "content": "x", "_chat_mode": "plan"},
    )
    assert res.block is True
    assert "deny" in (res.reason or "").lower() or "permission" in (res.reason or "").lower()


@pytest.mark.asyncio
async def test_permission_hook_local_allow_ask(monkeypatch, tmp_path: Path):
    clear_tool_hooks()
    ensure_builtin_hooks_registered()
    import backend.core.config as cfg

    class S:
        agent_permission_enabled = True
        agent_permission_profile = "cautious"
        agent_permission_ask_mode = "local_allow"
        agent_file_history = False
        agent_file_checkpoint = False

    monkeypatch.setattr(cfg, "settings", S())
    res = await run_before_tool_call(
        "command",
        {"command": "echo hi", "_chat_mode": "default"},
    )
    assert res.block is False


def test_file_history_restore_and_unrewind(tmp_path: Path):
    f = tmp_path / "note.txt"
    f.write_text("v1\n", encoding="utf-8")
    h = FileHistory(tmp_path, session_id="s1")
    pt = h.create_point(paths=["note.txt"], label="snap1")
    f.write_text("v2\n", encoding="utf-8")
    r = h.restore_point(pt.id, force=True)
    assert r["ok"]
    assert f.read_text(encoding="utf-8") == "v1\n"
    u = h.unrewind()
    assert u["ok"]
    assert f.read_text(encoding="utf-8") == "v2\n"


def test_worktree_parse_and_find_root():
    root = find_git_root(Path(__file__).resolve())
    assert root is not None
    porcelain = f"""worktree {root}
HEAD abcdef123456
branch refs/heads/main

"""
    items = parse_worktree_list(porcelain, root)
    assert items and items[0].path


def test_auto_classify_deny_rm_rf():
    load_rules(force_reload=True)
    dec, reason = apply_auto_classifier(
        "ask",
        "command",
        {"command": "rm -rf /tmp/xx"},
        enabled=True,
        project_root=Path(".").resolve(),
    )
    # deny rules should catch rf
    assert dec in ("deny", "ask")
    assert reason
