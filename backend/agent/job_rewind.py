"""Per work-order file rewind: snapshot touched files, restore on demand."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_lock = threading.RLock()
# inbox_item_id -> { point_id, session_id, root, paths }
_job_points: dict[str, dict[str, Any]] = {}


def _workspace_root() -> Path:
    try:
        from backend.tools.permissions import resolve_agent_workspace_root

        return Path(resolve_agent_workspace_root()).resolve()
    except Exception:
        return Path.cwd().resolve()


def begin_job_snapshot(inbox_item_id: str, *, label: str = "") -> dict[str, Any] | None:
    """Create empty history point at job start (files filled as tools write)."""
    iid = str(inbox_item_id)
    try:
        from backend.agent.file_history import FileHistory

        root = _workspace_root()
        hist = FileHistory(root, session_id=f"job-{iid}")
        pt = hist.create_point(paths=[], label=label or f"job-start:{iid[:8]}", kind="job_start")
        with _lock:
            _job_points[iid] = {
                "point_id": pt.id,
                "session_id": hist.session_id,
                "root": str(root),
                "paths": [],
            }
        return {"point_id": pt.id, "session_id": hist.session_id}
    except Exception as e:
        logger.debug("job_rewind begin skipped: %s", e)
        return None


def note_job_path(inbox_item_id: str, path: str) -> None:
    """Record a path that was written during the job (snapshot content if missing)."""
    iid = str(inbox_item_id)
    with _lock:
        meta = _job_points.get(iid)
    if not meta:
        return
    try:
        from backend.agent.file_history import FileHistory, _rel

        root = Path(meta["root"])
        hist = FileHistory(root, session_id=meta["session_id"])
        rel = _rel(path)
        # refresh start point sidecar by creating a new checkpoint with union paths
        paths = list(dict.fromkeys([*(meta.get("paths") or []), rel]))
        pt = hist.create_point(
            paths=paths,
            label=f"job-touch:{iid[:8]}",
            kind="job_touch",
            meta={"inbox_item_id": iid},
        )
        with _lock:
            meta["paths"] = paths
            meta["point_id"] = pt.id
            _job_points[iid] = meta
    except Exception as e:
        logger.debug("job_rewind note path: %s", e)


def rewind_job(inbox_item_id: str, *, force: bool = True) -> dict[str, Any]:
    """Restore files to the latest job checkpoint."""
    iid = str(inbox_item_id)
    with _lock:
        meta = _job_points.get(iid)
    if not meta:
        return {"ok": False, "error": "no rewind point for this job"}
    try:
        from backend.agent.file_history import FileHistory

        hist = FileHistory(Path(meta["root"]), session_id=meta["session_id"])
        return hist.restore_point(meta["point_id"], force=force)
    except Exception as e:
        return {"ok": False, "error": str(e)}


def job_rewind_info(inbox_item_id: str) -> dict[str, Any] | None:
    with _lock:
        m = _job_points.get(str(inbox_item_id))
        return dict(m) if m else None
