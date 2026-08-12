"""Minimal file checkpoints before write tools.

Copies existing files into ``.tevarn/checkpoints/<ts_uuid>/...`` under project root.
Each snapshot is registered under an **opaque checkpoint id** (UUID). Clients
only see the id; restore looks up the registry and enforces workspace bounds.
"""
from __future__ import annotations

import json
import logging
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_reg_lock = threading.Lock()


def _project_root() -> Path:
    try:
        from backend.tools.permissions import (
            resolve_agent_workspace_root,
        )

        return Path(resolve_agent_workspace_root())
    except Exception:
        return Path.cwd()


def _registry_path(root: Path | None = None) -> Path:
    r = (root or _project_root()) / ".tevarn" / "checkpoints"
    r.mkdir(parents=True, exist_ok=True)
    return r / "registry.json"


def _load_registry(root: Path | None = None) -> dict[str, Any]:
    path = _registry_path(root)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_registry(reg: dict[str, Any], root: Path | None = None) -> None:
    path = _registry_path(root)
    tmp = path.with_suffix(f".{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(json.dumps(reg, ensure_ascii=False, indent=0), encoding="utf-8")
        tmp.replace(path)
    except Exception as e:
        logger.debug("checkpoint registry save skip: %s", e)
        try:
            if tmp.is_file():
                tmp.unlink()
        except Exception:
            pass


def register_checkpoint(
    *,
    snapshot_path: str,
    target_path: str,
    tool: str = "",
) -> str:
    """Register a snapshot and return opaque id."""
    root = _project_root()
    cid = uuid.uuid4().hex
    with _reg_lock:
        reg = _load_registry(root)
        reg[cid] = {
            "snapshot": str(snapshot_path),
            "target": str(target_path),
            "tool": tool,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        # soft cap
        if len(reg) > 400:
            # drop oldest by created_at
            items = sorted(
                reg.items(),
                key=lambda kv: str((kv[1] or {}).get("created_at") or ""),
            )
            for k, _ in items[: len(reg) - 400]:
                reg.pop(k, None)
        _save_registry(reg, root)
    return cid


def lookup_checkpoint(checkpoint_id: str) -> dict[str, Any] | None:
    cid = (checkpoint_id or "").strip()
    if cid.startswith("rust:"):
        return {"backend": "rust", "id": cid[5:]}
    if not cid:
        return None
    with _reg_lock:
        reg = _load_registry()
        entry = reg.get(cid)
        if not entry:
            return None
        return {"backend": "python", "id": cid, **entry}


def _resolve_target(name: str, arguments: dict[str, Any]) -> Path | None:
    raw = (
        arguments.get("filepath")
        or arguments.get("path")
        or arguments.get("file")
        or arguments.get("file_path")
        or ""
    )
    raw = str(raw).strip()
    if not raw:
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
    """Snapshot file before write. Returns **opaque checkpoint id** (not path)."""
    target = _resolve_target(name, arguments)
    if target is None or not target.is_file():
        return None

    root = _project_root()
    try:
        rel = target.relative_to(root)
    except ValueError:
        rel = Path("_external") / target.name

    ts = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        + "_"
        + uuid.uuid4().hex[:8]
    )
    dest_root = root / ".tevarn" / "checkpoints" / ts
    dest = dest_root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, dest)
    idx = dest_root / "INDEX.txt"
    with idx.open("a", encoding="utf-8") as f:
        f.write(f"{name}\t{target}\t{dest}\n")
    cid = register_checkpoint(
        snapshot_path=str(dest),
        target_path=str(target),
        tool=name,
    )
    # also write id into index for operators
    with idx.open("a", encoding="utf-8") as f:
        f.write(f"id\t{cid}\t{dest}\n")
    return cid


def list_recent_checkpoints(limit: int = 20) -> list[str]:
    root = _project_root() / ".tevarn" / "checkpoints"
    if not root.is_dir():
        return []
    dirs = sorted([p for p in root.iterdir() if p.is_dir()], reverse=True)
    return [str(p) for p in dirs[:limit]]


def restore_checkpoint_file(snapshot_path_or_id: str) -> dict[str, Any]:
    """Restore by opaque id (preferred) or legacy snapshot path.

    Security:
      - registry targets must stay under project root
      - legacy path snapshots must live under .tevarn/checkpoints/
    """
    raw = str(snapshot_path_or_id or "").strip()
    if not raw:
        return {"ok": False, "error": "path or id required"}

    root = _project_root().resolve()
    cp_root = (root / ".tevarn" / "checkpoints").resolve()

    # Opaque id path
    entry = lookup_checkpoint(raw)
    if entry and entry.get("backend") == "python" and entry.get("snapshot"):
        snap = Path(str(entry["snapshot"])).expanduser()
        target = Path(str(entry.get("target") or "")).expanduser()
        try:
            snap = snap.resolve()
            target_res = target.resolve()
        except OSError:
            return {"ok": False, "error": "invalid registry paths"}
        try:
            snap.relative_to(cp_root)
            target_res.relative_to(root)
        except ValueError:
            return {"ok": False, "error": "registry path outside allowed roots"}
        if not snap.is_file():
            return {"ok": False, "error": "snapshot file missing"}
        try:
            target_res.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(snap, target_res)
            return {
                "ok": True,
                "restored": str(target_res),
                "from": str(snap),
                "checkpoint_id": raw,
                "backend": "python",
            }
        except Exception as e:
            return {"ok": False, "error": f"restore copy failed: {e}"}

    # Legacy: treat as filesystem snapshot path
    snap = Path(raw).expanduser()
    try:
        snap = snap.resolve()
    except OSError:
        return {"ok": False, "error": "invalid snapshot path"}
    if not snap.is_file():
        return {"ok": False, "error": f"snapshot not found: {snap}"}
    try:
        snap.relative_to(cp_root)
    except ValueError:
        return {"ok": False, "error": "snapshot outside .tevarn/checkpoints"}

    index_file: Path | None = None
    for parent in [snap.parent, *list(snap.parents)[:6]]:
        cand = parent / "INDEX.txt"
        if cand.is_file():
            try:
                cand.resolve().relative_to(cp_root)
            except ValueError:
                continue
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
            try:
                dest_p = Path(dest).resolve()
            except OSError:
                dest_p = None
            if dest == snap_s or dest_p == snap:
                if parts[0].strip() == "id":
                    continue
                target = Path(parts[1].strip())
                break
    except Exception as e:
        return {"ok": False, "error": f"read INDEX failed: {e}"}

    if target is None:
        return {"ok": False, "error": "no INDEX mapping for this snapshot"}

    try:
        target_res = target.expanduser().resolve()
        target_res.relative_to(root)
    except ValueError:
        return {"ok": False, "error": "restore target outside project root"}
    except OSError:
        return {"ok": False, "error": "invalid target path"}

    try:
        target_res.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(snap, target_res)
        return {
            "ok": True,
            "restored": str(target_res),
            "from": str(snap),
            "index": str(index_file),
            "backend": "python",
        }
    except Exception as e:
        return {"ok": False, "error": f"restore copy failed: {e}"}


__all__ = [
    "snapshot_path_for_tool",
    "list_recent_checkpoints",
    "restore_checkpoint_file",
    "register_checkpoint",
    "lookup_checkpoint",
]
