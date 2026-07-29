"""Merge profile base rules + user DSL + secret floor into one rule list.

Single entry for tool_hooks PermissionGate construction.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from backend.agent.dangerous_paths import secret_deny_rules
from backend.agent.permission_rules_dsl import rules_from_payload
from backend.agent.permissions_rules import PermissionRule, rules_for_profile

logger = logging.getLogger(__name__)

SETTING_KEY = "agent_permission_rules"


def _settings() -> Any:
    from backend.core.config import settings

    return settings


def load_user_rules_payload() -> dict[str, list[str]]:
    """Read {allow, ask, deny} from settings (in-memory first, then defaults)."""
    s = _settings()
    # direct attrs (runtime / env)
    allow = _as_str_list(getattr(s, "agent_permission_allow", None))
    ask = _as_str_list(getattr(s, "agent_permission_ask", None))
    deny = _as_str_list(getattr(s, "agent_permission_deny", None))
    # optional JSON blob
    blob = getattr(s, SETTING_KEY, None) or getattr(s, "agent_permission_rules_json", None)
    if isinstance(blob, str) and blob.strip():
        try:
            data = json.loads(blob)
            if isinstance(data, dict):
                allow = allow or _as_str_list(data.get("allow"))
                ask = ask or _as_str_list(data.get("ask"))
                deny = deny or _as_str_list(data.get("deny"))
        except Exception as e:
            logger.debug("permission rules json parse: %s", e)
    elif isinstance(blob, dict):
        allow = allow or _as_str_list(blob.get("allow"))
        ask = ask or _as_str_list(blob.get("ask"))
        deny = deny or _as_str_list(blob.get("deny"))
    return {"allow": allow, "ask": ask, "deny": deny}


def _as_str_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        # comma or newline separated
        parts = [p.strip() for p in v.replace("\n", ",").split(",")]
        return [p for p in parts if p]
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    return []


def build_effective_rules(profile: str) -> list[PermissionRule]:
    """profile base → user allow/ask → user deny → secret deny (last / hardest)."""
    base = list(rules_for_profile(profile))
    user = rules_from_payload(load_user_rules_payload())
    # user deny rules must win over free/always-approve base allow
    rules = base + [r for r in user if r.decision != "deny"] + [
        r for r in user if r.decision == "deny"
    ]
    relax = bool(getattr(_settings(), "agent_permission_relax_secrets", False))
    if not relax:
        rules.extend(secret_deny_rules())
    return rules


def describe_rules() -> dict[str, Any]:
    payload = load_user_rules_payload()
    return {
        "user": payload,
        "secrets_enforced": not bool(
            getattr(_settings(), "agent_permission_relax_secrets", False)
        ),
        "secret_patterns_count": len(secret_deny_rules()) // 2,
    }
