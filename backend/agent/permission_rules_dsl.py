"""Grok-style permission rule DSL: ToolPrefix(glob) → PermissionRule.

Examples:
  Bash(rm*)          deny shell commands starting with rm
  Edit(**/.env)      deny edits matching path
  Read(*.pem)        ask/deny reads of pem files
  MCPTool(sales__*)  deny MCP tools
  Bash               bare prefix = match all of that class
"""

from __future__ import annotations

import re
from typing import Any, Literal

from backend.agent.permissions_rules import (
    PERM_BASH,
    PERM_EDIT,
    PERM_READ,
    PERM_TASK,
    PermissionRule,
)

Decision = Literal["allow", "deny", "ask"]

_PREFIX_TO_KEY: dict[str, str] = {
    "bash": PERM_BASH,
    "shell": PERM_BASH,
    "command": PERM_BASH,
    "edit": PERM_EDIT,
    "write": PERM_EDIT,
    "read": PERM_READ,
    "grep": PERM_READ,
    "webfetch": "web_fetch",
    "web_fetch": "web_fetch",
    "mcptool": "mcp",
    "mcp": "mcp",
    "task": PERM_TASK,
    "agent": PERM_TASK,
}


def parse_rule_string(raw: str, decision: Decision) -> PermissionRule | None:
    """Parse one rule string into a PermissionRule. Returns None if empty/invalid."""
    s = (raw or "").strip()
    if not s:
        return None
    # ToolPrefix(pattern) or bare ToolPrefix
    m = re.match(r"^([A-Za-z_]+)\((.*)\)$", s)
    if m:
        prefix, pattern = m.group(1), m.group(2).strip() or "*"
    else:
        prefix, pattern = s, "*"
    key = _PREFIX_TO_KEY.get(prefix.lower(), prefix.lower())
    # Claude-style Bash(cmd:*) → prefix match on cmd
    if pattern.endswith(":*"):
        pattern = pattern[:-1] + "*"
    return PermissionRule(key=key, decision=decision, pattern=pattern)


def parse_rule_list(items: list[str] | None, decision: Decision) -> list[PermissionRule]:
    out: list[PermissionRule] = []
    for item in items or []:
        rule = parse_rule_string(str(item), decision)
        if rule is not None:
            out.append(rule)
    return out


def rules_from_payload(payload: dict[str, Any] | None) -> list[PermissionRule]:
    """{ allow: [...], ask: [...], deny: [...] } → ordered rules.

    Deny is appended last so last-match + separate strict overlay both work;
    callers should also run deny-first when merging.
    """
    data = payload or {}
    allow = parse_rule_list(data.get("allow") if isinstance(data.get("allow"), list) else [], "allow")
    ask = parse_rule_list(data.get("ask") if isinstance(data.get("ask"), list) else [], "ask")
    deny = parse_rule_list(data.get("deny") if isinstance(data.get("deny"), list) else [], "deny")
    # OpenCode last-match: broad → narrow; put deny after so it wins if same specificity
    return allow + ask + deny
