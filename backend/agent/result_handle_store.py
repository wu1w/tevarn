"""Session-scoped durable store for spilled tool-result handles.

Kernel ``ResultSpillStore`` is in-memory and keyed by process_id. Files under
``~/.tevarn/tool_results/{id}.txt`` are deleted when the kernel process ends
(``drop_process``). Each user turn starts a new kernel process, so handles
must also live in a session directory that survives process drop.

This module is the Python-side source of truth for paging via ``result_load``
later in the same chat session.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

HANDLE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_MAX_AGE_SECONDS = 7 * 24 * 3600


def session_key(session_id: str | None) -> str:
    """Sanitize session id for use as a single directory name."""
    raw = (session_id or "").strip().lower().replace("-", "")
    if not raw or "/" in raw or "\\" in raw or ".." in raw or "\x00" in raw:
        return "orphan"
    return raw


def store_root() -> Path:
    return Path.home() / ".tevarn" / "tool_results" / "sessions"


def session_dir(session_id: str | None) -> Path:
    return store_root() / session_key(session_id)


def _mint_id() -> str:
    return uuid.uuid4().hex[:16]


def _accept_handle_id(handle_id: str | None) -> str:
    hid = (handle_id or "").strip()
    if hid and HANDLE_ID_RE.match(hid):
        return hid
    return _mint_id()


def put(
    session_id: str | None,
    content: str,
    tool: str = "tool",
    handle_id: str | None = None,
) -> str:
    """Write full body + meta; return the handle id actually stored."""
    hid = _accept_handle_id(handle_id)
    d = session_dir(session_id)
    d.mkdir(parents=True, exist_ok=True)
    body = "" if content is None else str(content)
    txt = d / f"{hid}.txt"
    meta_path = d / f"{hid}.json"
    # Isolation: never write outside this session dir (hid already sanitized).
    txt.write_text(body, encoding="utf-8")
    payload = {
        "id": hid,
        "session_id": session_key(session_id),
        "tool": (tool or "tool"),
        "bytes": len(body.encode("utf-8")),
        "created_at": time.time(),
    }
    meta_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return hid


def load(session_id: str | None, handle_id: str) -> str:
    """Load body for ``handle_id`` in this session only.

    Raises
    ------
    ValueError
        Invalid id, or meta says a different session.
    KeyError
        No file under this session's directory.
    """
    hid = (handle_id or "").strip()
    if not hid or not HANDLE_ID_RE.match(hid):
        raise ValueError(f"invalid result handle: {hid!r}")
    d = session_dir(session_id)
    txt = d / f"{hid}.txt"
    meta_path = d / f"{hid}.json"
    try:
        d_res = d.resolve()
        txt_res = txt.resolve()
    except OSError as e:
        raise KeyError(hid) from e
    if not str(txt_res).startswith(str(d_res)):
        raise ValueError("handle path escapes session directory")
    if not txt.is_file():
        raise KeyError(hid)
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = None
        if isinstance(meta, dict):
            expected = session_key(session_id)
            got = str(meta.get("session_id") or "")
            if got and got != expected:
                raise ValueError("handle belongs to another session")
    return txt.read_text(encoding="utf-8")


def list_handles(session_id: str | None) -> list[dict[str, Any]]:
    """List live handles in this session (skips junk / 7d+ stale)."""
    d = session_dir(session_id)
    if not d.is_dir():
        return []
    now = time.time()
    out: list[dict[str, Any]] = []
    try:
        entries = list(d.glob("*.json"))
    except OSError:
        return []
    for meta_path in entries:
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if not isinstance(meta, dict):
                continue
            created = meta.get("created_at")
            if created is not None:
                try:
                    if now - float(created) > _MAX_AGE_SECONDS:
                        continue
                except (TypeError, ValueError):
                    pass
            hid = str(meta.get("id") or meta_path.stem)
            if not hid:
                continue
            try:
                nbytes = int(meta.get("bytes") or 0)
            except (TypeError, ValueError):
                nbytes = 0
            out.append(
                {
                    "id": hid,
                    "tool": str(meta.get("tool") or "tool"),
                    "bytes": nbytes,
                }
            )
        except Exception:
            continue
    return out


__all__ = [
    "HANDLE_ID_RE",
    "session_key",
    "store_root",
    "session_dir",
    "put",
    "load",
    "list_handles",
]
