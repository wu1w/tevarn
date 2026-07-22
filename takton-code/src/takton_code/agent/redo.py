"""Redo stack for rewind: capture pre-rewind disk state and restore it (/unrewind)."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class RedoEntry:
    id: str
    session_id: str
    point_id: str | None
    created_at: float
    files: dict[str, str | None]  # path -> content before rewind (None = did not exist)
    only_paths: list[str] | None = None
    label: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "point_id": self.point_id,
            "created_at": self.created_at,
            "files": self.files,
            "only_paths": self.only_paths,
            "label": self.label,
            "meta": self.meta,
            "file_count": len(self.files),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RedoEntry":
        return cls(
            id=str(d["id"]),
            session_id=str(d["session_id"]),
            point_id=d.get("point_id"),
            created_at=float(d.get("created_at") or 0),
            files=dict(d.get("files") or {}),
            only_paths=d.get("only_paths"),
            label=str(d.get("label") or ""),
            meta=dict(d.get("meta") or {}),
        )


class RedoStack:
    """Per-session LIFO stack persisted under file-history/<session>/redo.jsonl."""

    MAX_ENTRIES = 30

    def __init__(self, home: Path) -> None:
        self.home = Path(home)
        self.root = self.home / "file-history"
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        d = self.root / session_id
        d.mkdir(parents=True, exist_ok=True)
        return d / "redo.jsonl"

    def push(self, entry: RedoEntry) -> None:
        p = self._path(entry.session_id)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
        self._trim(entry.session_id)

    def _load_all(self, session_id: str) -> list[RedoEntry]:
        p = self._path(session_id)
        if not p.is_file():
            return []
        out: list[RedoEntry] = []
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(RedoEntry.from_dict(json.loads(line)))
            except Exception:
                continue
        return out

    def _trim(self, session_id: str) -> None:
        entries = self._load_all(session_id)
        if len(entries) <= self.MAX_ENTRIES:
            return
        keep = entries[-self.MAX_ENTRIES :]
        p = self._path(session_id)
        with p.open("w", encoding="utf-8") as f:
            for e in keep:
                f.write(json.dumps(e.to_dict(), ensure_ascii=False) + "\n")

    def list(self, session_id: str, limit: int = 20) -> list[RedoEntry]:
        entries = self._load_all(session_id)
        return list(reversed(entries[-limit:]))

    def pop(self, session_id: str) -> RedoEntry | None:
        entries = self._load_all(session_id)
        if not entries:
            return None
        last = entries[-1]
        keep = entries[:-1]
        p = self._path(session_id)
        with p.open("w", encoding="utf-8") as f:
            for e in keep:
                f.write(json.dumps(e.to_dict(), ensure_ascii=False) + "\n")
        return last

    def peek(self, session_id: str) -> RedoEntry | None:
        entries = self._load_all(session_id)
        return entries[-1] if entries else None


def capture_pre_state(
    root: Path,
    paths: list[str],
    *,
    read_file,
) -> dict[str, str | None]:
    """read_file(rel) -> (content|None)."""
    out: dict[str, str | None] = {}
    for p in paths:
        rel = p.replace("\\", "/").lstrip("./")
        try:
            out[rel] = read_file(rel)
        except Exception:
            out[rel] = None
    return out


def apply_redo_files(root: Path, files: dict[str, str | None]) -> list[str]:
    """Restore files from redo entry. Returns log lines."""
    root = root.resolve()
    logs: list[str] = []
    for rel, content in files.items():
        abs_p = (root / rel).resolve()
        try:
            abs_p.relative_to(root)
        except ValueError:
            logs.append(f"skip escape: {rel}")
            continue
        try:
            if content is None:
                if abs_p.is_file():
                    abs_p.unlink()
                    logs.append(f"deleted {rel}")
            else:
                abs_p.parent.mkdir(parents=True, exist_ok=True)
                abs_p.write_text(content, encoding="utf-8")
                logs.append(f"restored {rel}")
        except OSError as e:
            logs.append(f"{rel}: {e}")
    return logs


def new_entry(
    session_id: str,
    *,
    point_id: str | None,
    files: dict[str, str | None],
    only_paths: list[str] | None = None,
    label: str = "pre-rewind",
) -> RedoEntry:
    return RedoEntry(
        id=f"redo_{uuid.uuid4().hex[:10]}",
        session_id=session_id,
        point_id=point_id,
        created_at=time.time(),
        files=files,
        only_paths=only_paths,
        label=label,
    )
