"""Diff engine: snapshot before write, unified diff after, revert support."""

from __future__ import annotations

import difflib
import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class FileChange:
    path: str  # relative to project root
    abs_path: str
    before: str | None  # None = did not exist
    after: str | None  # None = deleted
    op: str  # create|modify|delete
    ts: float = field(default_factory=time.time)

    def unified_diff(self, context: int = 3) -> str:
        a = (self.before or "").splitlines(keepends=True)
        b = (self.after or "").splitlines(keepends=True)
        if not a and not b:
            return ""
        from_name = f"a/{self.path}"
        to_name = f"b/{self.path}"
        diff = difflib.unified_diff(a, b, fromfile=from_name, tofile=to_name, n=context)
        return "".join(diff)

    def summary(self) -> str:
        before_l = 0 if self.before is None else self.before.count("\n") + (1 if self.before else 0)
        after_l = 0 if self.after is None else self.after.count("\n") + (1 if self.after else 0)
        return f"{self.op:6} {self.path} ({before_l} → {after_l} lines)"


class DiffEngine:
    def __init__(self, project_root: Path) -> None:
        self.root = project_root.resolve()
        self._snapshots: dict[str, str | None] = {}  # rel path -> content before first touch this turn
        self.changes: list[FileChange] = []
        self.turn_changes: list[FileChange] = []

    def rel_of(self, path: Path | str) -> str:
        p = Path(path).resolve()
        try:
            return str(p.relative_to(self.root)).replace("\\", "/")
        except ValueError:
            return str(p).replace("\\", "/")

    def resolve(self, rel_or_abs: str) -> Path:
        p = Path(rel_or_abs)
        if not p.is_absolute():
            p = (self.root / p).resolve()
        else:
            p = p.resolve()
        # sandbox: must stay under root
        try:
            p.relative_to(self.root)
        except ValueError as e:
            raise PermissionError(f"path escapes project root: {p}") from e
        return p

    def snapshot_before(self, rel_or_abs: str) -> None:
        path = self.resolve(rel_or_abs)
        rel = self.rel_of(path)
        if rel in self._snapshots:
            return
        if path.is_file():
            try:
                self._snapshots[rel] = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                self._snapshots[rel] = None
        else:
            self._snapshots[rel] = None

    def record_after(self, rel_or_abs: str) -> FileChange | None:
        path = self.resolve(rel_or_abs)
        rel = self.rel_of(path)
        before = self._snapshots.get(rel, None)
        if before is None and rel not in self._snapshots:
            # no snapshot — try current as after only
            before = None
            self._snapshots[rel] = None

        after: str | None
        if path.is_file():
            try:
                after = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                after = None
        else:
            after = None

        if before == after:
            return None

        if before is None and after is not None:
            op = "create"
        elif before is not None and after is None:
            op = "delete"
        else:
            op = "modify"

        ch = FileChange(path=rel, abs_path=str(path), before=before, after=after, op=op)
        self.changes.append(ch)
        self.turn_changes.append(ch)
        return ch

    def begin_turn(self) -> None:
        self.turn_changes = []
        self._snapshots = {}

    def end_turn_summary(self) -> str:
        if not self.turn_changes:
            return "(no file changes this turn)"
        lines = [c.summary() for c in self.turn_changes]
        return "\n".join(lines)

    def all_diffs(self, limit_files: int = 50) -> str:
        parts: list[str] = []
        for ch in self.changes[-limit_files:]:
            d = ch.unified_diff()
            if d:
                parts.append(d)
            else:
                parts.append(f"# {ch.summary()}\n")
        return "\n".join(parts) if parts else "(no diffs)"

    def revert(self, rel_path: str) -> str:
        # find last change for path
        target = None
        for ch in reversed(self.changes):
            if ch.path == rel_path.replace("\\", "/") or ch.path.endswith(rel_path):
                target = ch
                break
        if not target:
            return f"no recorded change for {rel_path}"
        path = Path(target.abs_path)
        if target.before is None:
            if path.exists():
                path.unlink()
            msg = f"reverted create → deleted {target.path}"
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(target.before, encoding="utf-8")
            msg = f"reverted {target.path} to pre-change content"
        # record reverse change
        self.snapshot_before(str(path))
        # force snapshot as current after revert for bookkeeping
        self._snapshots[target.path] = target.after
        self.record_after(str(path))
        return msg

    def content_hash(self, text: str | None) -> str:
        if text is None:
            return "none"
        return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:12]

    def to_dict(self) -> dict[str, Any]:
        return {
            "change_count": len(self.changes),
            "changes": [
                {
                    "path": c.path,
                    "op": c.op,
                    "ts": c.ts,
                    "diff": c.unified_diff()[:8000],
                }
                for c in self.changes[-100:]
            ],
        }
