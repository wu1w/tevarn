"""Pending capability grants: worker blocked → CEO (steward) can approve.

When a workforce tool is denied for outside_identity_caps, we record a request
so crew_steward action=pending_grants / grant_caps can resolve it without the
owner clicking a flood of per-tool dialogs.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any

_lock = threading.RLock()
# id -> request dict
_REQUESTS: dict[str, dict[str, Any]] = {}
_MAX = 200


def record_cap_request(
    *,
    identity_id: str,
    identity_name: str = "",
    tool: str,
    needed_cap: str | None = None,
    reason: str = "",
    inbox_item_id: str | None = None,
    steward_session_id: str | None = None,
) -> dict[str, Any]:
    """Idempotent-ish: merge same identity+tool+open request."""
    with _lock:
        for r in _REQUESTS.values():
            if (
                r.get("status") == "pending"
                and r.get("identity_id") == str(identity_id)
                and r.get("tool") == str(tool)
            ):
                r["hits"] = int(r.get("hits") or 1) + 1
                r["updated_at"] = time.time()
                r["reason"] = (reason or r.get("reason") or "")[:500]
                if inbox_item_id:
                    r["inbox_item_id"] = inbox_item_id
                return dict(r)

        rid = f"cgr_{uuid.uuid4().hex[:12]}"
        rec = {
            "id": rid,
            "identity_id": str(identity_id),
            "identity_name": str(identity_name or ""),
            "tool": str(tool or ""),
            "needed_cap": needed_cap or "",
            "reason": (reason or "")[:500],
            "inbox_item_id": str(inbox_item_id or "") or None,
            "steward_session_id": str(steward_session_id or "") or None,
            "status": "pending",  # pending | granted | denied | cancelled
            "hits": 1,
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        _REQUESTS[rid] = rec
        # trim oldest resolved first, then oldest pending
        if len(_REQUESTS) > _MAX:
            ordered = sorted(_REQUESTS.values(), key=lambda x: float(x.get("updated_at") or 0))
            for old in ordered:
                if old.get("status") != "pending":
                    _REQUESTS.pop(str(old.get("id")), None)
                if len(_REQUESTS) <= _MAX:
                    break
        return dict(rec)


def list_pending(*, identity_id: str | None = None, limit: int = 40) -> list[dict[str, Any]]:
    with _lock:
        items = [dict(r) for r in _REQUESTS.values() if r.get("status") == "pending"]
    if identity_id:
        iid = str(identity_id)
        items = [r for r in items if r.get("identity_id") == iid]
    items.sort(key=lambda x: -float(x.get("updated_at") or 0))
    return items[: max(1, limit)]


def mark_request(
    request_id: str,
    *,
    status: str,
    by: str = "ceo",
) -> dict[str, Any] | None:
    with _lock:
        r = _REQUESTS.get(str(request_id))
        if not r:
            return None
        r["status"] = status
        r["resolved_by"] = by
        r["updated_at"] = time.time()
        return dict(r)


def mark_granted_for_identity(
    identity_id: str,
    *,
    caps: list[str] | None = None,
    tools: list[str] | None = None,
    by: str = "ceo",
) -> int:
    """Mark matching pending requests granted after a successful grant_caps."""
    want_caps = set(caps or [])
    want_tools = set(tools or [])
    n = 0
    with _lock:
        for r in _REQUESTS.values():
            if r.get("status") != "pending":
                continue
            if r.get("identity_id") != str(identity_id):
                continue
            hit = False
            if r.get("tool") and r["tool"] in want_tools:
                hit = True
            if r.get("needed_cap") and r["needed_cap"] in want_caps:
                hit = True
            if not want_caps and not want_tools:
                hit = True  # blanket grant mark for this identity
            if hit:
                r["status"] = "granted"
                r["resolved_by"] = by
                r["updated_at"] = time.time()
                n += 1
    return n


def reset_for_tests() -> None:
    with _lock:
        _REQUESTS.clear()
