"""Phase 3.2 permission_court 优先级与可解释字段矩阵。

优先级（详规）：
  secret_floor deny > user deny > skill > path > steward >
  user allow > profile > session_grant > default
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from backend.kernel.capability import CapabilityToken
from backend.kernel.permission_court import (
    CourtDecision,
    args_digest,
    decide_capability,
    decide_tool,
)
from backend.kernel.process import AgentProcess


def test_args_digest_stable():
    a = args_digest("file_read", {"path": "a.py", "_internal": 1})
    b = args_digest("file_read", {"path": "a.py", "_other": 2})
    assert a.startswith("sha256:")
    assert a == b  # 内部 _ 键不参与


def test_capability_unknown_and_terminal():
    d = decide_capability(
        process_id="x", action="tool_call", target="file_read", proc=None
    )
    assert d.verdict == "deny" and d.layer == "capability"
    assert d.matched_rule == "capability:unknown_process"
    audit = d.to_audit()
    assert {"tool", "args_digest", "verdict", "matched_rule", "layer"} <= set(audit)

    proc = AgentProcess(identity="t", state="completed")
    d2 = decide_capability(
        process_id=proc.id, action="tool_call", target="file_read", proc=proc
    )
    assert d2.verdict == "deny" and "terminal" in d2.matched_rule


def test_capability_token_scope_and_ok():
    proc = AgentProcess(identity="t", capabilities=["file_read", "grep"])
    proc.token = CapabilityToken(
        capabilities=frozenset({"file_read"}), process_id=proc.id
    )
    ok = decide_capability(
        process_id=proc.id, action="tool_call", target="file_read", proc=proc
    )
    assert ok.verdict == "allow" and ok.layer == "capability"
    deny = decide_capability(
        process_id=proc.id, action="tool_call", target="grep", proc=proc
    )
    assert deny.verdict == "deny" and "token_scope" in deny.matched_rule


def test_capability_token_expired():
    proc = AgentProcess(identity="t", capabilities=["file_read"])
    proc.token = CapabilityToken(
        capabilities=frozenset({"file_read"}),
        process_id=proc.id,
        expires_at=time.time() - 10,
    )
    d = decide_capability(
        process_id=proc.id, action="tool_call", target="file_read", proc=proc
    )
    assert d.verdict == "deny" and "expired" in d.matched_rule


def test_capability_set_miss():
    proc = AgentProcess(identity="t", capabilities=["file_read"])
    d = decide_capability(
        process_id=proc.id, action="tool_call", target="terminal", proc=proc
    )
    assert d.verdict == "deny" and "set_miss" in d.matched_rule


@pytest.mark.asyncio
async def test_decide_tool_disabled(monkeypatch):
    monkeypatch.setattr(
        "backend.core.config.settings.agent_permission_enabled",
        False,
        raising=False,
    )
    d = await decide_tool("file_write", {"path": "x.py"})
    assert d.verdict == "allow" and d.layer == "disabled"


@pytest.mark.asyncio
async def test_secret_floor_denies_env_path(monkeypatch):
    monkeypatch.setattr(
        "backend.core.config.settings.agent_permission_enabled",
        True,
        raising=False,
    )
    monkeypatch.setattr(
        "backend.core.config.settings.agent_permission_relax_secrets",
        False,
        raising=False,
    )
    d = await decide_tool("file_read", {"path": ".env"})
    assert d.verdict == "deny"
    assert d.layer == "secret_floor"
    assert d.matched_rule == "secret_floor"


@pytest.mark.asyncio
async def test_skill_contract_denies_tool(monkeypatch):
    monkeypatch.setattr(
        "backend.core.config.settings.agent_permission_enabled",
        True,
        raising=False,
    )

    # 绕过 secret/path：用不触发 path 的工具名
    contract = SimpleNamespace(tools=["file_read", "grep"], permissions={})
    d = await decide_tool(
        "file_write",
        {"path": "ok.txt"},
        skill_contract=contract,
    )
    assert d.verdict == "deny"
    assert d.layer == "skill"
    assert "skill:tools" in d.matched_rule


@pytest.mark.asyncio
async def test_skill_contract_permissions_deny(monkeypatch):
    monkeypatch.setattr(
        "backend.core.config.settings.agent_permission_enabled",
        True,
        raising=False,
    )
    contract = SimpleNamespace(
        tools=None,
        permissions={"deny": ["terminal"]},
    )
    d = await decide_tool("terminal", {"command": "ls"}, skill_contract=contract)
    assert d.verdict == "deny" and d.layer == "skill"


@pytest.mark.asyncio
async def test_path_layer_denies_outside_workspace(monkeypatch):
    monkeypatch.setattr(
        "backend.core.config.settings.agent_permission_enabled",
        True,
        raising=False,
    )

    class _Mgr:
        def is_path_allowed(self, path: str) -> bool:
            return False

    monkeypatch.setattr(
        "backend.tools.permissions.ToolPermissionManager",
        lambda: _Mgr(),
    )
    d = await decide_tool("file_read", {"path": "C:/Windows/System32/config"})
    # 可能先被 secret 或 path 拦；至少 deny
    assert d.verdict == "deny"
    assert d.layer in ("path", "secret_floor", "profile", "user_deny")


@pytest.mark.asyncio
async def test_court_decision_audit_shape():
    d = CourtDecision(
        tool="t",
        args_digest="sha256:x",
        verdict="allow",
        matched_rule="capability:set_ok",
        layer="capability",
        reason="ok",
        capability_checked=True,
    )
    a = d.to_audit()
    assert a["verdict"] == "allow"
    assert a["layer"] == "capability"
    assert a["matched_rule"] == "capability:set_ok"
