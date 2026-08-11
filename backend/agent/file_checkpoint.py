"""Minimal file checkpoints before write tools.

Copies existing files into ``.tevarn/checkpoints/<timestamp>/...`` under project root.
"""
from __future__ import annotations

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _project_root() -> Path:
    try:
        from backend.tools.permissions import (
            resolve_agent_workspace_root,
        )

        return Path(resolve_agent_workspace_root())
    except Exception:
        return Path.cwd()


def _resolve_target(name: str, arguments: dict[str, Any]) -> Path | None:
    raw = (
        arguments.get("filepath")
        or arguments.get("path")
        or arguments.get("file")
        or ""
    )
    raw = str(raw).strip()
    if not raw:
        # apply_patch may embed paths — skip if no single path
        return None
    root = _project_root()
    p = Path(raw)
    if not p.is_absolute():
        p = root / p
    try:
        p = p.resolve()
    except OSError:
        return None
    return p


def snapshot_path_for_tool(name: str, arguments: dict[str, Any]) -> str | None:
    """If target exists, copy to checkpoint dir; return snapshot path or None."""
    target = _resolve_target(name, arguments)
    if target is None or not target.is_file():
        return None

    root = _project_root()
    try:
        rel = target.relative_to(root)
    except ValueError:
        # outside root — still checkpoint under .tevarn with flat name
        rel = Path("_external") / target.name

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest_root = root / ".tevarn" / "checkpoints" / ts
    dest = dest_root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, dest)
    # tiny index
    idx = dest_root / "INDEX.txt"
    with idx.open("a", encoding="utf-8") as f:
        f.write(f"{name}\t{target}\t{dest}\n")
    return str(dest)


def list_recent_checkpoints(limit: int = 20) -> list[str]:
    root = _project_root() / ".tevarn" / "checkpoints"
    if not root.is_dir():
        return []
    dirs = sorted([p for p in root.iterdir() if p.is_dir()], reverse=True)
    return [str(p) for p in dirs[:limit]]


def restore_checkpoint_file(snapshot_path: str) -> dict[str, Any]:
    """Restore a file from a Python-side checkpoint snapshot path.

    Uses INDEX.txt written by snapshot_path_for_tool:
      tool_name\ttarget_abs\tsnapshot_abs
    """
    snap = Path(str(snapshot_path or "")).expanduser()
    try:
        snap = snap.resolve()
    except OSError:
        return {"ok": False, "error": "invalid snapshot path"}
    if not snap.is_file():
        return {"ok": False, "error": f"snapshot not found: {snap}"}

    # Walk up a few levels for INDEX.txt (nested rel paths under ts dir)
    index_file: Path | None = None
    for parent in [snap.parent, *list(snap.parents)[:4]]:
        cand = parent / "INDEX.txt"
        if cand.is_file():
            index_file = cand
            break
    if index_file is None:
        return {"ok": False, "error": "INDEX.txt not found near snapshot"}

    target: Path | None = None
    snap_s = str(snap)
    try:
        for line in index_file.read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            dest = parts[2].strip()
            if dest == snap_s or Path(dest).resolve() == snap:
                target = Path(parts[1].strip())
                break
    except Exception as e:
        return {"ok": False, "error": f"read INDEX failed: {e}"}

    if target is None:
        return {"ok": False, "error": "no INDEX mapping for this snapshot"}

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(snap, target)
        return {
            "ok": True,
            "restored": str(target),
            "from": str(snap),
            "index": str(index_file),
        }
    except Exception as e:
        return {"ok": False, "error": f"restore copy failed: {e}"}


__all__ = [
    "snapshot_path_for_tool",
    "list_recent_checkpoints",
    "restore_checkpoint_file",
]

