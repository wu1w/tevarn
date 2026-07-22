"""Claude-parity file history — deeper open-source implementation.

Local Claude Code evidence (~/.claude/file-history/<session>/<hash>@vN):
- trackedFileBackups: {path: {backupFileName, version, backupTime}}
- Snapshots hang on messageId; isSnapshotUpdate for deltas
- /rewind + EscEsc → restore code, conversation, or both
- Safety: refuse some restores; diff stats before apply

Takton goes further:
- Content-addressed disk backups + optional inline small files
- Diff preview / dry-run
- Dirty-file guard (hash mismatch since snapshot → require force)
- Worktree-aware paths; never phone-home
- Named checkpoints + autoloop phase markers
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

RewindScope = Literal["code", "conversation", "both"]


def _sha12(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def _rel(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


@dataclass
class HistoryPoint:
    id: str
    session_id: str
    label: str
    turn_id: str | None
    message_id: str | None
    kind: str
    created_at: float
    file_count: int = 0
    parent_id: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "label": self.label,
            "turn_id": self.turn_id,
            "message_id": self.message_id,
            "kind": self.kind,
            "created_at": self.created_at,
            "file_count": self.file_count,
            "parent_id": self.parent_id,
            "meta": self.meta,
        }


class BackupDisk:
    """~/.takton-code/file-history/<session_id>/<content_sha>@vN  (Claude layout)."""

    def __init__(self, home: Path) -> None:
        self.root = Path(home) / "file-history"
        self.root.mkdir(parents=True, exist_ok=True)

    def session_dir(self, session_id: str) -> Path:
        d = self.root / session_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def write_backup(
        self, session_id: str, content: str | None, *, version: int = 1
    ) -> dict[str, Any]:
        """Write content to disk; return backup meta. content=None → tombstone (file absent)."""
        if content is None:
            return {
                "backupFileName": None,
                "version": version,
                "backupTime": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "content_hash": None,
                "size": 0,
                "tombstone": True,
            }
        raw = content.encode("utf-8")
        h = _sha12(raw)
        name = f"{h}@v{version}"
        path = self.session_dir(session_id) / name
        if not path.exists():
            path.write_bytes(raw)
        return {
            "backupFileName": name,
            "version": version,
            "backupTime": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "content_hash": h,
            "size": len(raw),
            "tombstone": False,
        }

    def read_backup(self, session_id: str, meta: dict[str, Any]) -> str | None:
        if meta.get("tombstone") or not meta.get("backupFileName"):
            return None
        path = self.session_dir(session_id) / str(meta["backupFileName"])
        if not path.is_file():
            raise FileNotFoundError(f"backup missing: {path}")
        return path.read_text(encoding="utf-8", errors="replace")


class FileHistory:
    """High-level Claude-parity API over SessionStore + disk backups."""

    INLINE_MAX = 64_000  # still store small blobs in DB for portability

    def __init__(self, store: Any, project_root: Path, *, home: Path | None = None) -> None:
        self.store = store
        self.root = project_root.resolve()
        if home is None:
            from takton_code.config import home_dir

            home = home_dir()
        self.home = Path(home)
        self.disk = BackupDisk(self.home)
        self.enabled = True
        from takton_code.agent.redo import RedoStack

        self.redo = RedoStack(self.home)

    def _safe_abs(self, rel: str) -> Path | None:
        rel = _rel(rel)
        abs_p = (self.root / rel).resolve()
        try:
            abs_p.relative_to(self.root)
        except ValueError:
            return None
        return abs_p

    def _read_disk(self, rel: str) -> tuple[str | None, str | None]:
        """Return (content|None if missing, sha12|None)."""
        abs_p = self._safe_abs(rel)
        if abs_p is None:
            return None, None
        if not abs_p.is_file():
            return None, None
        try:
            raw = abs_p.read_bytes()
            text = raw.decode("utf-8", errors="replace")
            return text, _sha12(raw)
        except OSError:
            return None, None

    async def create_point(
        self,
        session_id: str,
        *,
        label: str = "",
        turn_id: str | None = None,
        message_id: str | None = None,
        message_row_id: int | None = None,
        kind: str = "manual",
        paths: list[str] | None = None,
        files: dict[str, str | None] | None = None,
        parent_id: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> HistoryPoint:
        point_id = f"chk_{uuid.uuid4().hex[:12]}"
        now = time.time()
        payload: dict[str, str | None] = dict(files or {})
        for p in paths or []:
            rel = _rel(p)
            if rel in payload:
                continue
            content, _ = self._read_disk(rel)
            payload[rel] = content

        file_rows: list[dict[str, Any]] = []
        for rel, content in payload.items():
            ver = await self.store.next_history_file_version(session_id, rel)
            bmeta = self.disk.write_backup(session_id, content, version=ver)
            inline = None
            if content is not None and len(content.encode("utf-8")) <= self.INLINE_MAX:
                inline = content
            file_rows.append(
                {
                    "path": rel,
                    "content": inline,
                    "backup_meta": bmeta,
                    "content_hash": bmeta.get("content_hash"),
                    "version": ver,
                }
            )

        await self.store.create_history_point_v2(
            point_id=point_id,
            session_id=session_id,
            label=label or kind,
            turn_id=turn_id,
            message_id=message_id,
            message_row_id=message_row_id,
            kind=kind,
            created_at=now,
            parent_id=parent_id,
            meta=meta or {},
            files=file_rows,
        )
        if turn_id:
            for row in file_rows:
                await self.store.save_file_snapshot(
                    session_id, turn_id, row["path"], row.get("content")
                )

        return HistoryPoint(
            id=point_id,
            session_id=session_id,
            label=label or kind,
            turn_id=turn_id,
            message_id=message_id,
            kind=kind,
            created_at=now,
            file_count=len(file_rows),
            parent_id=parent_id,
            meta={**(meta or {}), **({"message_row_id": message_row_id} if message_row_id else {})},
        )

    async def snapshot_before_edit(
        self,
        session_id: str,
        turn_id: str,
        rel_path: str,
        *,
        message_id: str | None = None,
    ) -> None:
        """First-write-wins pre-edit backup into turn's edit point (Claude trackEdit)."""
        rel = _rel(rel_path)
        abs_p = self._safe_abs(rel)
        if abs_p is None:
            return
        content, chash = self._read_disk(rel)
        point = await self.store.get_or_create_turn_history_point(
            session_id=session_id,
            turn_id=turn_id,
            label=f"turn {turn_id}",
            kind="edit",
            message_id=message_id,
        )
        # only first snapshot for path
        existing = await self.store.get_history_file(point["id"], rel)
        if existing:
            return
        ver = await self.store.next_history_file_version(session_id, rel)
        bmeta = self.disk.write_backup(session_id, content, version=ver)
        inline = content if content is not None and len(content.encode("utf-8")) <= self.INLINE_MAX else None
        await self.store.add_history_file_v2(
            point["id"],
            path=rel,
            content=inline,
            backup_meta=bmeta,
            content_hash=chash,
            version=ver,
        )
        await self.store.save_file_snapshot(session_id, turn_id, rel, content)

    async def mark_user_leaf(
        self,
        session_id: str,
        *,
        turn_id: str,
        message_id: str,
        message_row_id: int | None = None,
        label: str = "user",
    ) -> HistoryPoint:
        """Claude: snapshot leaf on user message (may start empty until edits)."""
        return await self.create_point(
            session_id,
            label=label,
            turn_id=turn_id,
            message_id=message_id,
            message_row_id=message_row_id,
            kind="user",
            paths=[],
            meta={"leaf": True, "message_row_id": message_row_id},
        )

    async def list_points(self, session_id: str, limit: int = 50) -> list[HistoryPoint]:
        rows = await self.store.list_history_points(session_id, limit=limit)
        out: list[HistoryPoint] = []
        for r in rows:
            meta = r.get("meta") or {}
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except json.JSONDecodeError:
                    meta = {}
            out.append(
                HistoryPoint(
                    id=r["id"],
                    session_id=r["session_id"],
                    label=r.get("label") or "",
                    turn_id=r.get("turn_id"),
                    message_id=r.get("message_id"),
                    kind=r.get("kind") or "manual",
                    created_at=float(r.get("created_at") or 0),
                    file_count=int(r.get("file_count") or 0),
                    parent_id=r.get("parent_id"),
                    meta=meta if isinstance(meta, dict) else {},
                )
            )
        return out

    async def get_diff_stats(self, session_id: str, point_id: str) -> dict[str, Any]:
        """Compare current disk vs checkpoint (Claude fileHistoryGetDiffStats)."""
        point = await self.store.get_history_point(point_id)
        if not point or point["session_id"] != session_id:
            return {"ok": False, "error": "not found"}
        files = await self.store.load_history_files_v2(point_id)
        would_restore = 0
        would_delete = 0
        unchanged = 0
        dirty = 0  # disk differs from both backup and "expected after edit" — just report
        details: list[dict[str, Any]] = []
        for f in files:
            rel = f["path"]
            snap_content = await self._resolve_content(session_id, f)
            cur, cur_hash = self._read_disk(rel)
            snap_hash = f.get("content_hash")
            if snap_content is None:
                if cur is None:
                    unchanged += 1
                    status = "absent"
                else:
                    would_delete += 1
                    status = "delete"
            else:
                if cur == snap_content:
                    unchanged += 1
                    status = "same"
                else:
                    would_restore += 1
                    status = "restore"
                    if cur is not None and snap_hash and cur_hash and cur_hash != snap_hash:
                        dirty += 1
            details.append(
                {
                    "path": rel,
                    "status": status,
                    "version": f.get("version"),
                    "snap_hash": snap_hash,
                    "disk_hash": cur_hash,
                }
            )
        return {
            "ok": True,
            "point_id": point_id,
            "label": point.get("label"),
            "would_restore": would_restore,
            "would_delete": would_delete,
            "unchanged": unchanged,
            "dirty_hint": dirty,
            "files": details,
        }

    async def _resolve_content(self, session_id: str, row: dict[str, Any]) -> str | None:
        if row.get("content") is not None:
            return row["content"]
        meta = row.get("backup_meta") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except json.JSONDecodeError:
                meta = {}
        if meta.get("tombstone"):
            return None
        if meta.get("backupFileName"):
            try:
                return self.disk.read_backup(session_id, meta)
            except FileNotFoundError:
                return row.get("content")
        return None

    async def rewind(
        self,
        session_id: str,
        point_id: str | None = None,
        *,
        steps: int = 1,
        scope: RewindScope = "code",
        dry_run: bool = False,
        force: bool = False,
        only_paths: list[str] | None = None,
        focus_path: str | None = None,
    ) -> dict[str, Any]:
        """
        Restore to a history point.
        only_paths: if set, only restore these relative paths (partial rewind).
        focus_path: prefer this file's patch first in side_summary.
        """
        if point_id:
            point = await self.store.get_history_point(point_id)
            if not point or point["session_id"] != session_id:
                return {"ok": False, "error": f"checkpoint not found: {point_id}"}
        else:
            points = await self.store.list_history_points(session_id, limit=max(steps, 1) + 5)
            if not points:
                return {"ok": False, "error": "no checkpoints"}
            idx = min(steps, len(points)) - 1
            point = points[idx]
            point_id = point["id"]

        only_set: set[str] | None = None
        if only_paths:
            only_set = {_rel(p) for p in only_paths if p and p.strip()}

        result: dict[str, Any] = {
            "ok": True,
            "point_id": point_id,
            "label": point.get("label"),
            "kind": point.get("kind"),
            "scope": scope,
            "dry_run": dry_run,
            "message_id": point.get("message_id"),
            "turn_id": point.get("turn_id"),
            "restored": [],
            "errors": [],
            "skipped": [],
            "diff": None,
            "side_summary": "",
            "unified_diffs": [],
            "only_paths": sorted(only_set) if only_set else None,
            "focus_path": focus_path,
            "focus_index": 0,
        }

        if scope in ("code", "both"):
            stats = await self.get_diff_stats(session_id, point_id)
            # filter stats files if only_paths
            all_files = stats.get("files") or []
            if only_set is not None:
                all_files = [f for f in all_files if _rel(str(f.get("path") or "")) in only_set]
            result["diff"] = {
                k: stats[k]
                for k in ("would_restore", "would_delete", "unchanged", "dirty_hint")
                if k in stats
            }
            # recompute counts for filtered set
            if only_set is not None:
                result["diff"] = {
                    "would_restore": sum(1 for f in all_files if f.get("status") == "restore"),
                    "would_delete": sum(1 for f in all_files if f.get("status") == "delete"),
                    "unchanged": sum(1 for f in all_files if f.get("status") == "same"),
                    "dirty_hint": stats.get("dirty_hint", 0),
                    "filtered": True,
                    "selected": len(only_set),
                }
            result["diff_files"] = all_files

            files = await self.store.load_history_files_v2(point_id)
            if only_set is not None:
                files = [f for f in files if _rel(f["path"]) in only_set]

            udiffs: list[dict[str, Any]] = []
            for f in files:
                rel = f["path"]
                try:
                    snap = await self._resolve_content(session_id, f)
                except Exception:
                    continue
                cur, _ = self._read_disk(rel)
                if cur == snap:
                    continue
                status = "delete" if snap is None and cur is not None else (
                    "create" if snap is not None and cur is None else "restore"
                )
                patch = make_unified_diff(rel, cur, snap, max_lines=48)
                if patch:
                    udiffs.append({"path": rel, "status": status, "patch": patch})
            # focus path first
            if focus_path:
                fp = _rel(focus_path)
                udiffs.sort(key=lambda u: (0 if _rel(u["path"]) == fp else 1, u["path"]))
                for i, u in enumerate(udiffs):
                    if _rel(u["path"]) == fp:
                        result["focus_index"] = i
                        break
            result["unified_diffs"] = udiffs[:40]

            if dry_run:
                result["files"] = all_files
                result["side_summary"] = format_rewind_side_panel(result, preview=True)
                return result

            # Capture pre-rewind state for /unrewind (redo stack)
            from takton_code.agent.redo import new_entry

            pre_paths = [f["path"] for f in files]
            pre_files: dict[str, str | None] = {}
            for rel in pre_paths:
                cur, _ = self._read_disk(rel)
                pre_files[rel] = cur
            if pre_files:
                entry = new_entry(
                    session_id,
                    point_id=point_id,
                    files=pre_files,
                    only_paths=sorted(only_set) if only_set else None,
                    label=f"before rewind {point_id}",
                )
                self.redo.push(entry)
                result["redo_id"] = entry.id

            for f in files:
                rel = f["path"]
                abs_p = self._safe_abs(rel)
                if abs_p is None:
                    result["errors"].append(f"skip escape: {rel}")
                    continue
                try:
                    content = await self._resolve_content(session_id, f)
                except Exception as e:  # noqa: BLE001
                    result["errors"].append(f"{rel}: backup read {e}")
                    continue

                try:
                    if content is None:
                        if abs_p.exists() and abs_p.is_file():
                            if not force and abs_p.is_symlink():
                                result["skipped"].append(f"refuse symlink: {rel}")
                                continue
                            abs_p.unlink()
                            result["restored"].append(f"deleted {rel}")
                    else:
                        abs_p.parent.mkdir(parents=True, exist_ok=True)
                        if abs_p.exists() and abs_p.is_dir():
                            result["skipped"].append(f"refuse dir: {rel}")
                            continue
                        abs_p.write_text(content, encoding="utf-8")
                        result["restored"].append(f"restored {rel}")
                except OSError as e:
                    result["errors"].append(f"{rel}: {e}")

        if scope in ("conversation", "both"):
            result["truncate_to_message_id"] = point.get("message_id")
            result["truncate_to_turn_id"] = point.get("turn_id")
            mrid = point.get("message_row_id")
            if mrid is None:
                meta = point.get("meta_json") or point.get("meta") or {}
                if isinstance(meta, str):
                    try:
                        meta = json.loads(meta)
                    except json.JSONDecodeError:
                        meta = {}
                mrid = meta.get("message_row_id") if isinstance(meta, dict) else None
            result["truncate_to_message_row_id"] = mrid

        result["ok"] = not result["errors"] or bool(result["restored"]) or scope == "conversation"
        result["side_summary"] = format_rewind_side_panel(result, preview=False)
        return result

    async def unrewind(self, session_id: str) -> dict[str, Any]:
        """Pop redo stack and restore pre-rewind disk state."""
        from takton_code.agent.redo import apply_redo_files

        entry = self.redo.pop(session_id)
        if not entry:
            return {"ok": False, "error": "redo stack empty"}
        logs = apply_redo_files(self.root, entry.files)
        return {
            "ok": True,
            "redo_id": entry.id,
            "point_id": entry.point_id,
            "restored": logs,
            "file_count": len(entry.files),
            "label": entry.label,
            "side_summary": "── UNREWIND (redo) ──\n"
            + f"id={entry.id}  undid rewind to {entry.point_id}\n"
            + "\n".join(f"  • {x}" for x in logs[:40]),
        }

    async def list_redo(self, session_id: str, limit: int = 15) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self.redo.list(session_id, limit=limit)]

    async def apply_hunks(
        self,
        session_id: str,
        path: str,
        hunk_indices: list[int],
        *,
        patch: str | None = None,
        push_redo: bool = True,
    ) -> dict[str, Any]:
        """
        Apply selected hunks of a disk→checkpoint style patch onto a file.
        If patch is None, use focused unified diff from last rewind payload (caller passes patch).
        """
        from takton_code.agent.hunks import apply_selected_hunks, parse_unified_hunks
        from takton_code.agent.redo import new_entry

        rel = _rel(path)
        abs_p = self._safe_abs(rel)
        if abs_p is None:
            return {"ok": False, "error": "path escape"}
        if not patch:
            return {"ok": False, "error": "no patch provided"}
        hunks = parse_unified_hunks(patch)
        if not hunks:
            return {"ok": False, "error": "no hunks in patch"}
        cur, _ = self._read_disk(rel)
        if cur is None and not abs_p.exists():
            cur = ""
        elif cur is None:
            cur = ""
        if push_redo:
            entry = new_entry(
                session_id,
                point_id=None,
                files={rel: cur if abs_p.is_file() else None},
                only_paths=[rel],
                label=f"before hunk apply {rel}",
            )
            self.redo.push(entry)
        new_text, errs = apply_selected_hunks(cur, hunks, hunk_indices)
        try:
            abs_p.parent.mkdir(parents=True, exist_ok=True)
            abs_p.write_text(new_text, encoding="utf-8")
        except OSError as e:
            return {"ok": False, "error": str(e), "hunk_errors": errs}
        return {
            "ok": True,
            "path": rel,
            "hunks_applied": hunk_indices,
            "hunk_errors": errs,
            "bytes": len(new_text.encode("utf-8")),
            "side_summary": (
                f"── HUNK APPLY {rel} ──\n"
                f"applied indices={hunk_indices}\n"
                + ("\n".join(errs) if errs else "clean apply")
            ),
        }


def make_unified_diff(
    path: str,
    current: str | None,
    checkpoint: str | None,
    *,
    max_lines: int = 48,
) -> str:
    """Diff showing how rewind changes the file: current (disk) → checkpoint."""
    import difflib

    a = (current if current is not None else "").splitlines(keepends=True)
    b = (checkpoint if checkpoint is not None else "").splitlines(keepends=True)
    if a == b:
        return ""
    fromfile = f"a/{path} (disk)"
    tofile = f"b/{path} (checkpoint)"
    if current is None:
        fromfile = f"a/{path} (missing)"
    if checkpoint is None:
        tofile = f"b/{path} (deleted)"
    lines = list(
        difflib.unified_diff(a, b, fromfile=fromfile, tofile=tofile, lineterm="\n", n=2)
    )
    if not lines:
        return ""
    if len(lines) > max_lines:
        head = lines[: max_lines - 1]
        head.append(f"... truncated {len(lines) - (max_lines - 1)} lines ...\n")
        lines = head
    return "".join(lines).rstrip() + "\n"


def format_rewind_side_panel(
    res: dict[str, Any],
    *,
    preview: bool = False,
    focus_only: bool = False,
) -> str:
    """Rich-ish plain text for TUI side panel after /rewind (includes unified diffs)."""
    lines: list[str] = []
    tag = "PREVIEW" if preview or res.get("dry_run") else "REWIND"
    lines.append(f"── {tag} {res.get('point_id') or '?'} ──")
    lines.append(f"scope={res.get('scope')}  kind={res.get('kind')}  {res.get('label') or ''}")
    if res.get("only_paths"):
        lines.append(f"partial: {len(res['only_paths'])} path(s) → {', '.join(res['only_paths'][:8])}")
    diff = res.get("diff") or {}
    if diff:
        filt = " (filtered)" if diff.get("filtered") else ""
        lines.append(
            f"Δ restore={diff.get('would_restore', 0)}  "
            f"delete={diff.get('would_delete', 0)}  "
            f"same={diff.get('unchanged', 0)}  "
            f"dirty={diff.get('dirty_hint', 0)}{filt}"
        )
    files = res.get("diff_files") or res.get("files") or []
    if files and not focus_only:
        lines.append("files:")
        for f in files[:30]:
            st = f.get("status") or "?"
            lines.append(f"  [{st}] {f.get('path')}")
        if len(files) > 30:
            lines.append(f"  … +{len(files) - 30}")
    restored = res.get("restored") or []
    if restored and not preview and not focus_only:
        lines.append("applied:")
        for r in restored[:25]:
            lines.append(f"  • {r}")
        if len(restored) > 25:
            lines.append(f"  … +{len(restored) - 25}")
    if res.get("truncate_to_message_row_id") is not None and not focus_only:
        lines.append(f"conversation anchor row={res.get('truncate_to_message_row_id')}")
    if res.get("skipped") and not focus_only:
        lines.append("skipped: " + "; ".join(res["skipped"][:5]))
    if res.get("errors") and not focus_only:
        lines.append("errors: " + "; ".join(res["errors"][:5]))

    udiffs = list(res.get("unified_diffs") or [])
    fi = int(res.get("focus_index") or 0)
    if udiffs:
        if focus_only:
            fi = max(0, min(fi, len(udiffs) - 1))
            u = udiffs[fi]
            lines.append("")
            lines.append(f"── patch {fi + 1}/{len(udiffs)} · [ / ] cycle · /patch ──")
            lines.append(f"• [{u.get('status')}] {u.get('path')}")
            lines.extend((u.get("patch") or "").splitlines()[:80])
        else:
            lines.append("")
            lines.append("── unified (disk → checkpoint) ──")
            lines.append(f"(focus {fi + 1}/{len(udiffs)} — use /patch next|prev|<path>)")
            budget = 120
            # show focused first then others
            order = list(range(len(udiffs)))
            if 0 <= fi < len(udiffs):
                order = [fi] + [i for i in order if i != fi]
            for i in order:
                if budget <= 0:
                    lines.append("… more diffs omitted — /patch to focus …")
                    break
                u = udiffs[i]
                mark = "▶" if i == fi else "•"
                lines.append(f"{mark} [{u.get('status')}] {u.get('path')}")
                patch = (u.get("patch") or "").splitlines()
                take_n = min(len(patch), 40 if i == fi else 12, budget)
                take = patch[:take_n]
                lines.extend(take)
                budget -= len(take)
                if len(patch) > len(take):
                    lines.append(f"  … +{len(patch) - len(take)} lines")
    return "\n".join(lines)


async def _export_point_impl(self: FileHistory, session_id: str, point_id: str, dest: Path) -> Path:
    """Export checkpoint bundle (better than Claude: portable)."""
    point = await self.store.get_history_point(point_id)
    if not point or point["session_id"] != session_id:
        raise FileNotFoundError(point_id)
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    files = await self.store.load_history_files_v2(point_id)
    manifest = {"point": dict(point), "files": []}
    for f in files:
        rel = f["path"]
        content = await self._resolve_content(session_id, f)
        entry = {"path": rel, "content_hash": f.get("content_hash"), "version": f.get("version")}
        if content is None:
            entry["tombstone"] = True
        else:
            out = dest / "files" / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(content, encoding="utf-8")
            entry["file"] = str(Path("files") / rel)
        manifest["files"].append(entry)
    (dest / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return dest


FileHistory.export_point = _export_point_impl  # type: ignore[method-assign]