"""Minimal file checkpoints before write tools.

Copies existing files into ``.takton/checkpoints/<timestamp>/...`` under project root.
"""
from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _project_root() -> Path:
    try:
        from backend.tools.permissions import detect_project_root, resolve_agent_workspace_root

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
        # outside root — still checkpoint under .takton with flat name
        rel = Path("_external") / target.name

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest_root = root / ".takton" / "checkpoints" / ts
    dest = dest_root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, dest)
    # tiny index
    idx = dest_root / "INDEX.txt"
    with idx.open("a", encoding="utf-8") as f:
        f.write(f"{name}\t{target}\t{dest}\n")
    return str(dest)


def list_recent_checkpoints(limit: int = 20) -> list[str]:
    root = _project_root() / ".takton" / "checkpoints"
    if not root.is_dir():
        return []
    dirs = sorted([p for p in root.iterdir() if p.is_dir()], reverse=True)
    return [str(p) for p in dirs[:limit]]


__all__ = ["snapshot_path_for_tool", "list_recent_checkpoints"]
