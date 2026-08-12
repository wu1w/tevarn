"""Per-session control inbox: steer / queue without killing the current run.

Multi-worker (same host):
  Durable spill under ``~/.tevarn/control_inbox/`` with cross-platform file locks
  so another uvicorn worker can see steer/queue. Cross-host still needs sticky
  sessions or a shared volume for ``~/.tevarn``.

Steer lifecycle (crash-safe):
  claim_steers() moves items to ``claimed``; after the loop injects them,
  ack_claimed() drops claimed. If the process dies between claim and ack,
  next claim re-queues claimed back into steers (at-least-once).
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
        from backend.core.config import get_tevarn_home
        base = get_tevarn_home() / "control_inbox"
        base.mkdir(parents=True, exist_ok=True)
        return base
    except Exception:
        try:
            base = get_tevarn_home() / "control_inbox"
            base.mkdir(parents=True, exist_ok=True)
            return base
        except Exception:
            base = Path.cwd() / ".tevarn" / "control_inbox"
            base.mkdir(parents=True, exist_ok=True)
            return base


def _spill_path(session_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)[:80]
    return _inbox_dir() / f"{safe}.json"


def _load_spill(session_id: str) -> dict[str, list]:
    path = _spill_path(session_id)
    if not path.is_file():
        return {"steers": [], "pending": [], "claimed": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"steers": [], "pending": [], "claimed": []}
        return {
            "steers": list(data.get("steers") or []),
            "pending": list(data.get("pending") or []),
            "claimed": list(data.get("claimed") or []),
        }
    except Exception as e:
        logger.debug("control_inbox load spill skip: %s", e)
        return {"steers": [], "pending": [], "claimed": []}


def _save_spill(
    session_id: str,
    steers: list,
    pending: list,
    claimed: list | None = None,
) -> None:
    path = _spill_path(session_id)
    # unique tmp avoids multi-worker clobber
    tmp = path.with_name(
        f"{path.stem}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
    )
    payload = {
        "steers": steers,
        "pending": pending,
        "claimed": list(claimed or []),
        "updated_at": time.time(),
    }
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(str(tmp), str(path))
    except Exception as e:
        logger.debug("control_inbox save spill skip: %s", e)
        try:
            if tmp.is_file():
                tmp.unlink()
        except Exception:
            pass


class _FileLock:
    """Cross-platform exclusive lock (fcntl on Unix, msvcrt on Windows)."""

    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path
        self._fh: Any = None

    def __enter__(self) -> "_FileLock":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.lock_path, "a+", encoding="utf-8")
        try:
            import fcntl

            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
            return self
        except ImportError:
            pass
        try:
            import msvcrt

            # lock one byte at start of file
            self._fh.seek(0)
            if self._fh.read(1) == "":
                self._fh.write("0")
                self._fh.flush()
            self._fh.seek(0)
            msvcrt.locking(self._fh.fileno(), msvcrt.LK_LOCK, 1)
            return self
        except Exception:
            # last resort: spin on exclusive create
            self._fh.close()
            self._fh = None
            for _ in range(100):
                try:
                    fd = os.open(
                        str(self.lock_path) + ".x",
                        os.O_CREAT | os.O_EXCL | os.O_RDWR,
                    )
                    self._fh = fd
                    return self
                except FileExistsError:
                    time.sleep(0.02)
            return self

    def __exit__(self, *args: Any) -> None:
        try:
            if self._fh is not None and not isinstance(self._fh, int):
                try:
                    import fcntl

                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
                except Exception:
                    try:
                        import msvcrt

                        self._fh.seek(0)
                        msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                    except Exception:
                        pass
                self._fh.close()
            elif isinstance(self._fh, int):
                try:
                    os.close(self._fh)
                except Exception:
                    pass
                try:
                    os.unlink(str(self.lock_path) + ".x")
                except Exception:
                    pass
        except Exception:
            pass


def _with_file_lock(session_id: str, fn: Any) -> Any:
    path = _spill_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _FileLock(path.with_suffix(".lock")):
        return fn()


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
            data["steers"] = data["steers"][-32:]
            _save_spill(
                self._session_id,
                data["steers"],
                data["pending"],
                data.get("claimed"),
            )

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
            _save_spill(
                self._session_id,
                data["steers"],
                data["pending"],
                data.get("claimed"),
            )

        with self._lock:
            _with_file_lock(self._session_id, _do)

    def claim_steers(self) -> list[ControlMessage]:
        """Move steers (+ leftover claimed) into claimed; return for injection."""
        out: list[ControlMessage] = []

        def _do() -> None:
            nonlocal out
            data = _load_spill(self._session_id)
            # recover un-acked claimed from prior crash
            recovered = list(data.get("claimed") or [])
            fresh = list(data.get("steers") or [])
            claimed = recovered + fresh
            _save_spill(self._session_id, [], data.get("pending") or [], claimed)
            out = [ControlMessage.from_dict(x) for x in claimed if x]

        with self._lock:
            _with_file_lock(self._session_id, _do)
        return out

    def ack_claimed(self) -> None:
        """Drop claimed steers after successful injection."""

        def _do() -> None:
            data = _load_spill(self._session_id)
            _save_spill(
                self._session_id,
                data.get("steers") or [],
                data.get("pending") or [],
                [],
            )

        with self._lock:
            _with_file_lock(self._session_id, _do)

    def drain_steers(self) -> list[ControlMessage]:
        """Backward-compat: claim + immediate ack (preferred: claim then ack)."""
        items = self.claim_steers()
        if items:
            self.ack_claimed()
        return items

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
            _save_spill(
                self._session_id,
                data.get("steers") or [],
                pending,
                data.get("claimed") or [],
            )
            result = ControlMessage.from_dict(first)

        with self._lock:
            _with_file_lock(self._session_id, _do)
        return result

    def clear_queue(self) -> int:
        """Drop all pending queue items (e.g. on stop). Returns dropped count."""
        n = 0

        def _do() -> None:
            nonlocal n
            data = _load_spill(self._session_id)
            n = len(data.get("pending") or [])
            _save_spill(
                self._session_id,
                data.get("steers") or [],
                [],
                data.get("claimed") or [],
            )

        with self._lock:
            _with_file_lock(self._session_id, _do)
        return n

    def clear(self) -> None:
        def _do() -> None:
            _save_spill(self._session_id, [], [], [])
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
    """Short controller-layer note; include attachment hints from meta."""
    if not steers:
        return ""

    def _one(s: ControlMessage) -> str:
        body = s.content
        atts = (s.meta or {}).get("attachments")
        if isinstance(atts, list) and atts:
            bits: list[str] = []
            for a in atts[:8]:
                if not isinstance(a, dict):
                    continue
                name = a.get("filename") or a.get("name") or a.get("url") or "file"
                url = a.get("url") or ""
                tc = (a.get("text_content") or "")[:1500]
                if tc:
                    bits.append(f"- {name}:\n{tc}")
                elif url:
                    bits.append(f"- {name}: {url}")
                else:
                    bits.append(f"- {name}")
            if bits:
                body = body + "\n[Attachments]\n" + "\n".join(bits)
        return body

    if len(steers) == 1:
        return (
            "[User steer — adjust the current task direction now]\n"
            + _one(steers[0])
        )
    lines = ["[User steers — apply in order]"]
    for i, s in enumerate(steers, 1):
        lines.append(f"{i}. {_one(s)}")
    return "\n".join(lines)
