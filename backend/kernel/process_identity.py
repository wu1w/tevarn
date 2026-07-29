"""Single source of truth: process identity_key ↔ agent identity.

Process keys look like:
  - wf:{uuid}           workforce job run
  - sub:{uuid}          nested subagent
  - main / human names  legacy

UI and org aggregation must use these helpers — never compare only by display name.
"""

from __future__ import annotations

import re
from typing import Any

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$"
)


def norm_uuid(value: str | None) -> str:
    s = (value or "").strip().lower().replace("-", "")
    return s


def workforce_key(identity_id: str | Any) -> str:
    return f"wf:{str(identity_id).strip()}"


def process_belongs_to(
    process_identity: str | None,
    *,
    agent_id: str | None = None,
    agent_name: str | None = None,
) -> bool:
    pid = (process_identity or "").strip()
    if not pid:
        return False
    name = (agent_name or "").strip()
    if name and pid == name:
        return True
    aid = str(agent_id or "").strip()
    if not aid:
        return False
    if pid == workforce_key(aid):
        return True
    compact = norm_uuid(aid)
    if pid == f"wf:{compact}" or pid == f"wf:{aid}":
        return True
    if pid.startswith(f"wf:{aid}") or pid.startswith(f"wf:{aid[:8]}"):
        return True
    if compact and compact in norm_uuid(pid.replace("wf:", "")):
        return True
    if aid in pid:
        return True
    return False


def sum_tokens_for_agent(
    processes: list[dict[str, Any]] | list[Any],
    *,
    agent_id: str,
    agent_name: str | None = None,
) -> int:
    total = 0
    for p in processes:
        if isinstance(p, dict):
            ident = p.get("identity") or p.get("identity_key")
            used = p.get("tokens_used")
        else:
            ident = getattr(p, "identity", None) or getattr(p, "identity_key", None)
            used = getattr(p, "tokens_used", 0)
        if process_belongs_to(str(ident or ""), agent_id=agent_id, agent_name=agent_name):
            total += int(used or 0)
    return total
