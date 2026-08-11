"""Per-session control inbox: steer / queue without killing the current run.

GPT-audit P0: while an agent is running, user messages can
  - steer  → inject into the live loop at the next safe boundary
  - queue  → run after the current turn finishes
  - stop   → cooperative cancel (existing path)
  - interrupt → stop current run and start a fresh one (legacy default)

Multi-worker (same host):
  In-memory registry is L1; durable spill under ``~/.tevarn/control_inbox/``
  with ``fcntl`` file locks so another uvicorn worker can see steer/queue.
  Optional Redis is NOT required. Cross-host still needs sticky sessions
  or a shared volume for ``~/.tevarn``.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ControlKind(str, Enum):
    STEER = "steer"
    QUEUE = "queue"
    STOP = "stop"
    INTERRUPT = "interrupt"


@dataclass
class ControlMessage:
    kind: ControlKind
    content: str
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value if isinstance(self.kind, ControlKind) else str(self.kind),
            "content": self.content,
            "meta": dict(self.meta or {}),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ControlMessage:
        kind_raw = str(d.get("kind") or "steer").lower()
        try:
            kind = ControlKind(kind_raw)
        except ValueError:
            kind = ControlKind.STEER
        return cls(
            kind=kind,
            content=str(d.get("content") or ""),
            meta=dict(d.get("meta") or {}),
        )


def _inbox_dir() -> Path:
    try:
        base = Path.home() / ".tevarn" / "control_inbox"
        base.mkdir(parents=True, exist_ok=True)
        return base
    except Exception:
        # last resort: cwd
        base = Path.cwd() / ".tevarn" / "control_inbox"
        base.mkdir(parents=True, exist_ok=True)
        return base


def _spill_path(session_id: str) -> Path:
    # sanitize filename
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)[:80]
    return _inbox_dir() / f"{safe}.json"


def _load_spill(session_id: str) -> dict[str, list[dict[str, Any]]]:
    path = _spill_path(session_id)
    if not path.is_file():
        return {"steers": [], "pending": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"steers": [], "pending": []}
        return {
            "steers": list(data.get("steers") or []),
            "pending": list(data.get("pending") or []),
        }
    except Exception as e:
        logger.debug("control_inbox load spill skip: %s", e)
        return {"steers": [], "pending": []}


def _save_spill(session_id: str, steers: list[dict], pending: list[dict]) -> None:
    path = _spill_path(session_id)
    tmp = path.with_suffix(".tmp")
    payload = {
        "steers": steers,
        "pending": pending,
        "updated_at": time.time(),
    }
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except Exception as e:
        logger.debug("control_inbox save spill skip: %s", e)
        try:
            if tmp.is_file():
                tmp.unlink()
        except Exception:
            pass


def _with_file_lock(session_id: str, fn: Any) -> Any:
    """Exclusive lock around spill read-modify-write (POSIX)."""
    path = _spill_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".lock")
    try:
        import fcntl  # Unix
    except ImportError:
        # Windows / no fcntl: best-effort without lock
        return fn()

    with open(lock_path, "a+", encoding="utf-8") as lf:
        try:
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            return fn()
        finally:
            try:
                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass


class SessionControlInbox:
    """Thread-safe + cross-worker (same host) steer/queue for one session."""

    def __init__(self, session_id: str) -> None:
        self._session_id = session_id
        self._lock = threading.Lock()

    def push_steer(self, content: str, *, meta: dict[str, Any] | None = None) -> None:
        text = (content or "").strip()
        if not text:
            return
        msg = ControlMessage(ControlKind.STEER, text, dict(meta or {})).to_dict()

        def _do() -> None:
            data = _load_spill(self._session_id)
            data["steers"].append(msg)
            # soft cap
            data["steers"] = data["steers"][-32:]
            _save_spill(self._session_id, data["steers"], data["pending"])

        with self._lock:
            _with_file_lock(self._session_id, _do)

    def push_queue(self, content: str, *, meta: dict[str, Any] | None = None) -> None:
        text = (content or "").strip()
        if not text:
            return
        msg = ControlMessage(ControlKind.QUEUE, text, dict(meta or {})).to_dict()

        def _do() -> None:
            data = _load_spill(self._session_id)
            data["pending"].append(msg)
            data["pending"] = data["pending"][-16:]
            _save_spill(self._session_id, data["steers"], data["pending"])

        with self._lock:
            _with_file_lock(self._session_id, _do)

    def drain_steers(self) -> list[ControlMessage]:
        out: list[ControlMessage] = []

        def _do() -> None:
            nonlocal out
            data = _load_spill(self._session_id)
            raw = list(data.get("steers") or [])
            data["steers"] = []
            _save_spill(self._session_id, data["steers"], data.get("pending") or [])
            out = [ControlMessage.from_dict(x) for x in raw if x]

        with self._lock:
            _with_file_lock(self._session_id, _do)
        return out

    def peek_pending_count(self) -> int:
        with self._lock:
            data = _load_spill(self._session_id)
            return len(data.get("pending") or [])

    def pop_queued(self) -> ControlMessage | None:
        result: ControlMessage | None = None

        def _do() -> None:
            nonlocal result
            data = _load_spill(self._session_id)
            pending = list(data.get("pending") or [])
            if not pending:
                result = None
                return
            first = pending.pop(0)
            _save_spill(self._session_id, data.get("steers") or [], pending)
            result = ControlMessage.from_dict(first)

        with self._lock:
            _with_file_lock(self._session_id, _do)
        return result

    def clear(self) -> None:
        def _do() -> None:
            _save_spill(self._session_id, [], [])
            path = _spill_path(self._session_id)
            try:
                if path.is_file():
                    path.unlink()
            except Exception:
                pass

        with self._lock:
            _with_file_lock(self._session_id, _do)


_registry: dict[str, SessionControlInbox] = {}
_reg_lock = threading.Lock()


def _key(session_id: uuid.UUID | str) -> str:
    return str(session_id)


def get_inbox(session_id: uuid.UUID | str) -> SessionControlInbox:
    k = _key(session_id)
    with _reg_lock:
        box = _registry.get(k)
        if box is None:
            box = SessionControlInbox(k)
            _registry[k] = box
        return box


def drop_inbox(session_id: uuid.UUID | str) -> None:
    k = _key(session_id)
    with _reg_lock:
        box = _registry.pop(k, None)
        if box is not None:
            box.clear()


def format_steer_block(steers: list[ControlMessage]) -> str:
    """Short controller-layer note for the model (not a long system essay)."""
    if not steers:
        return ""
    if len(steers) == 1:
        return (
            "[User steer — adjust the current task direction now]\n"
            f"{steers[0].content}"
        )
    lines = ["[User steers — apply in order]"]
    for i, s in enumerate(steers, 1):
        lines.append(f"{i}. {s.content}")
    return "\n".join(lines)
