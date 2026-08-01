"""Optional disk persistence for chat run snapshots (crash / multi-worker recovery).

Enabled when settings.agent_run_snapshot_persist is True (default True for durability).
Files: {TAKTON_HOME or ~/.takton}/run_snapshots/{session_id}.json
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _enabled() -> bool:
    try:
        from backend.core.config import settings

        return bool(getattr(settings, "agent_run_snapshot_persist", True))
    except Exception:
        return True


def _dir() -> Path:
    try:
        from backend.agent._takton_paths import host_home

        root = Path(os.environ.get("TAKTON_HOME") or (host_home() / ".takton"))
    except Exception:
        root = Path(os.environ.get("TAKTON_HOME") or Path.home() / ".takton")
    d = root / "run_snapshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(session_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(session_id))
    return _dir() / f"{safe}.json"


def save_snapshot(session_id: str, data: dict[str, Any]) -> None:
    if not _enabled() or not session_id:
        return
    try:
        payload = {
            **data,
            "session_id": str(session_id),
            "persisted_at": time.time(),
        }
        p = _path(session_id)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    except Exception as e:
        logger.debug("run_snapshot save skip: %s", e)


def load_snapshot(session_id: str, *, max_age_sec: float = 3600.0) -> dict[str, Any] | None:
    if not _enabled() or not session_id:
        return None
    try:
        p = _path(session_id)
        if not p.is_file():
            return None
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        ts = float(raw.get("persisted_at") or raw.get("updated_at") or 0)
        if ts and (time.time() - ts) > max_age_sec:
            try:
                p.unlink(missing_ok=True)  # type: ignore[call-arg]
            except Exception:
                pass
            return None
        return raw
    except Exception as e:
        logger.debug("run_snapshot load skip: %s", e)
        return None


def delete_snapshot(session_id: str) -> None:
    if not session_id:
        return
    try:
        p = _path(session_id)
        if p.is_file():
            p.unlink()
    except Exception as e:
        logger.debug("run_snapshot delete skip: %s", e)
