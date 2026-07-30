
"""Disk-backed file history + redo (Batch2 simplified, no SessionStore).

Snapshots under ``~/.takton/file-history/<session_id>/`` and project
``.takton/file-history/``. Complements ``file_checkpoint`` pre-write copies.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.agent._takton_paths import home_dir
from backend.agent.redo import RedoEntry, RedoStack

logger = logging.getLogger(__name__)


def _sha12(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def _rel(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


@dataclass
class HistoryPoint:
    id: str
    session_id: str
    label: str
    created_at: float
    files: dict[str, str | None] = field(default_factory=dict)  # rel -> content or None missing
    kind: str = "auto"
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "label": self.label,
            "created_at": self.created_at,
            "kind": self.kind,
            "file_count": len(self.files),
            "paths": list(self.files.keys()),
            "meta": self.meta,
        }


class FileHistory:
    """In-process + JSONL index file history for a project root."""

    def __init__(self, project_root: Path, *, session_id: str = "default") -> None:
        self.root = Path(project_root).resolve()
        self.session_id = session_id or "default"
        self.home = home_dir()
        self.redo = RedoStack(self.home)
        self._points: list[HistoryPoint] = []
        self._index_path = self._session_dir() / "index.jsonl"
        self._load_index()

    def _session_dir(self) -> Path:
        d = self.home / "file-history" / self.session_id
        d.mkdir(parents=True, exist_ok=True)
        # also project-local
        pd = self.root / ".takton" / "file-history" / self.session_id
        pd.mkdir(parents=True, exist_ok=True)
        return d

    def _safe_abs(self, rel: str) -> Path | None:
        rel = _rel(rel)
        abs_p = (self.root / rel).resolve()
        try:
            abs_p.relative_to(self.root)
        except ValueError:
            return None
        return abs_p

    def _read(self, rel: str) -> str | None:
        p = self._safe_abs(rel)
        if p is None or not p.is_file():
            return None
        try:
            return p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def _load_index(self) -> None:
        if not self._index_path.is_file():
            return
        try:
            for line in self._index_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                d = json.loads(line)
                # files stored as sidecar
                files_path = self._session_dir() / f"{d['id']}.json"
                files = {}
                if files_path.is_file():
                    files = json.loads(files_path.read_text(encoding="utf-8"))
                self._points.append(
                    HistoryPoint(
                        id=d["id"],
                        session_id=d.get("session_id") or self.session_id,
                        label=d.get("label") or "",
                        created_at=float(d.get("created_at") or 0),
                        files=files,
                        kind=d.get("kind") or "auto",
                        meta=d.get("meta") or {},
                    )
                )
        except Exception as e:
            logger.debug("file_history index load: %s", e)

    def create_point(
        self,
        *,
        paths: list[str] | None = None,
        files: dict[str, str | None] | None = None,
        label: str = "",
        kind: str = "auto",
        meta: dict[str, Any] | None = None,
    ) -> HistoryPoint:
        payload: dict[str, str | None] = dict(files or {})
        for p in paths or []:
            rel = _rel(p)
            if rel not in payload:
                payload[rel] = self._read(rel)
        pid = f"chk_{uuid.uuid4().hex[:12]}"
        pt = HistoryPoint(
            id=pid,
            session_id=self.session_id,
            label=label or kind,
            created_at=time.time(),
            files=payload,
            kind=kind,
            meta=meta or {},
        )
        self._points.append(pt)
        # persist
        sd = self._session_dir()
        (sd / f"{pid}.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        with self._index_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(pt.to_dict(), ensure_ascii=False) + "\n")
        # mirror to project
        try:
            pd = self.root / ".takton" / "file-history" / self.session_id
            pd.mkdir(parents=True, exist_ok=True)
            (pd / f"{pid}.json").write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass
        return pt

    def list_points(self, limit: int = 20) -> list[dict[str, Any]]:
        return [p.to_dict() for p in self._points[-limit:]][::-1]

    def restore_point(
        self,
        point_id: str,
        *,
        force: bool = False,
        only_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        pt = next((p for p in self._points if p.id == point_id), None)
        if not pt:
            return {"ok": False, "error": f"point not found: {point_id}"}
        # capture current for redo
        paths = list(only_paths or pt.files.keys())
        current: dict[str, str | None] = {}
        for rel in paths:
            current[_rel(rel)] = self._read(rel)
        entry = RedoEntry(
            id=f"redo_{uuid.uuid4().hex[:10]}",
            session_id=self.session_id,
            point_id=point_id,
            created_at=time.time(),
            files=current,
            only_paths=list(paths),
            label=f"before restore {point_id}",
        )
        self.redo.push(entry)

        restored = []
        errors = []
        for rel, content in pt.files.items():
            if only_paths and rel not in {_rel(p) for p in only_paths}:
                continue
            abs_p = self._safe_abs(rel)
            if abs_p is None:
                errors.append(f"escape: {rel}")
                continue
            try:
                if content is None:
                    if abs_p.exists():
                        abs_p.unlink()
                        restored.append(f"deleted {rel}")
                else:
                    abs_p.parent.mkdir(parents=True, exist_ok=True)
                    abs_p.write_text(content, encoding="utf-8")
                    restored.append(f"restored {rel}")
            except OSError as e:
                errors.append(f"{rel}: {e}")
        return {"ok": not errors, "restored": restored, "errors": errors, "redo_id": entry.id}

    def unrewind(self) -> dict[str, Any]:
        """Apply latest redo entry (undo the restore)."""
        entries = self.redo.list(self.session_id, limit=1)
        if not entries:
            return {"ok": False, "error": "redo stack empty"}
        e = entries[0]
        # pop: rewrite without last — simple: restore files then leave stack
        done = []
        for rel, content in (e.files or {}).items():
            abs_p = self._safe_abs(rel)
            if abs_p is None:
                continue
            if content is None:
                if abs_p.exists():
                    abs_p.unlink()
                    done.append(f"removed {rel}")
            else:
                abs_p.parent.mkdir(parents=True, exist_ok=True)
                abs_p.write_text(content, encoding="utf-8")
                done.append(f"rewound {rel}")
        # trim last from redo file
        all_e = self.redo._load_all(self.session_id)
        if all_e:
            keep = all_e[:-1]
            p = self.redo._path(self.session_id)
            with p.open("w", encoding="utf-8") as f:
                for x in keep:
                    f.write(json.dumps(x.to_dict(), ensure_ascii=False) + "\n")
        return {"ok": True, "restored": done}


# process-local histories keyed by session
_HISTORIES: dict[str, FileHistory] = {}


def get_file_history(project_root: Path | str, session_id: str = "default") -> FileHistory:
    key = f"{Path(project_root).resolve()}::{session_id}"
    h = _HISTORIES.get(key)
    if h is None:
        h = FileHistory(Path(project_root), session_id=session_id)
        _HISTORIES[key] = h
    return h
