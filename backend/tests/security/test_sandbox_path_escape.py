"""沙箱路径逃逸面回归测试（Phase 1.1）。

冻结真实行为，取自：
- backend/tools/permissions.py: ToolPermissionManager.is_path_allowed
- backend/services/tools/executors.py: _is_within
- backend/agent/dangerous_paths.py: secret_deny_rules / _SECRET_GLOBS

原则：用 manager.workspace_root 作为已解析基准来构造"内/外"路径，
避免依赖 resolve_agent_workspace_root 的具体归一化结果。
"""

from __future__ import annotations

import os

from backend.agent.dangerous_paths import secret_deny_rules
from backend.agent.permissions_rules import PERM_EDIT, PERM_READ
from backend.services.tools.executors import _is_within
from backend.tools.permissions import ToolPermissionManager

# ── ToolPermissionManager.is_path_allowed ─────────────────────────────

def test_path_inside_workspace_allowed(tmp_path) -> None:
    mgr = ToolPermissionManager(workspace_root=str(tmp_path))
    inside = os.path.join(mgr.workspace_root, "sub", "file.txt")
    assert mgr.is_path_allowed(inside, allowed_paths=[mgr.workspace_root]) is True


def test_workspace_root_itself_allowed(tmp_path) -> None:
    mgr = ToolPermissionManager(workspace_root=str(tmp_path))
    assert mgr.is_path_allowed(mgr.workspace_root, allowed_paths=[mgr.workspace_root]) is True


def test_parent_dir_escape_denied(tmp_path) -> None:
    mgr = ToolPermissionManager(workspace_root=str(tmp_path))
    outside = os.path.dirname(mgr.workspace_root.rstrip("\\/"))
    assert mgr.is_path_allowed(outside, allowed_paths=[mgr.workspace_root]) is False


def test_dotdot_traversal_denied(tmp_path) -> None:
    mgr = ToolPermissionManager(workspace_root=str(tmp_path))
    escaped = os.path.join(mgr.workspace_root, "..", "..", "etc_like_target")
    assert mgr.is_path_allowed(escaped, allowed_paths=[mgr.workspace_root]) is False


def test_absolute_outside_denied(tmp_path) -> None:
    mgr = ToolPermissionManager(workspace_root=str(tmp_path))
    # 一个与 workspace 明确不同的绝对根路径
    other = os.path.abspath(os.sep) if os.name != "nt" else "C:\\Windows\\System32"
    # 保证它不在 workspace 内
    if _is_within(other, mgr.workspace_root):
        other = os.path.join(mgr.workspace_root, "..", "definitely_outside")
    assert mgr.is_path_allowed(other, allowed_paths=[mgr.workspace_root]) is False


# ── _is_within（executors 内部路径闸门）────────────────────────────────

def test_is_within_true_for_child(tmp_path) -> None:
    child = os.path.join(str(tmp_path), "a", "b.txt")
    assert _is_within(child, str(tmp_path)) is True


def test_is_within_false_for_traversal(tmp_path) -> None:
    escaped = os.path.join(str(tmp_path), "..", "sibling", "x.txt")
    assert _is_within(escaped, str(tmp_path)) is False


# ── secret_deny_rules：凭证/私钥默认 deny ─────────────────────────────

def test_secret_deny_rules_deny_env_read_and_edit() -> None:
    rules = secret_deny_rules()
    denied_read = {
        r.pattern for r in rules if r.decision == "deny" and r.key == PERM_READ
    }
    denied_edit = {
        r.pattern for r in rules if r.decision == "deny" and r.key == PERM_EDIT
    }
    # .env 与 SSH 私钥必须在 deny 列表
    assert ".env" in denied_read
    assert ".env" in denied_edit
    assert any("id_rsa" in p for p in denied_read)
    assert any(".ssh" in p for p in denied_read)


def test_secret_deny_rules_allow_env_example_read() -> None:
    rules = secret_deny_rules()
    allow_read = {
        r.pattern for r in rules if r.decision == "allow" and r.key == PERM_READ
    }
    assert any("env.example" in p for p in allow_read)
