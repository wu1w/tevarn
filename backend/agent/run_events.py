"""Unified run event emission with **atomic** durable seq + spill.

Seq allocation and spill append happen under the same cross-process file lock:
  lock → read max seq from spill → n+1 → write line → unlock

This prevents multi-worker / multi-process duplicate seqs.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_mem_lock = threading.Lock()
_seq: dict[str, int] = {}
_MAX_SPILL_LINES = 500


def _spill_dir() -> Path:
    try:
        from backend.core.config import get_tevarn_home

        p = get_tevarn_home() / "run_events"
        p.mkdir(parents=True, exist_ok=True)
        return p
    except Exception:
        try:
            p = get_tevarn_home() / "run_events"
            p.mkdir(parents=True, exist_ok=True)
            return p
        except Exception:
            p = Path.cwd() / ".tevarn" / "run_events"
            p.mkdir(parents=True, exist_ok=True)
            return p


def _spill_path(session_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)[:64]
    return _spill_dir() / f"{safe}.jsonl"


def _max_seq_from_file(path: Path) -> int:
    if not path.is_file():
        return 0
    max_s = 0
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    s = int(obj.get("seq") or 0)
                    if s > max_s:
                        max_s = s
                except Exception:
                    continue
    except Exception as e:
        logger.debug("spill max seq read skip: %s", e)
    return max_s


class _SpillFileLock:
    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path
        self._fh: Any = None

    def __enter__(self) -> "_SpillFileLock":
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

            self._fh.seek(0)
            if self._fh.read(1) == "":
                self._fh.write("0")
                self._fh.flush()
            self._fh.seek(0)
            msvcrt.locking(self._fh.fileno(), msvcrt.LK_LOCK, 1)
            return self
        except Exception:
            # last-resort exclusive create
            try:
                self._fh.close()
            except Exception:
                pass
            for _ in range(200):
                try:
                    fd = os.open(
                        str(self.lock_path) + ".x",
                        os.O_CREAT | os.O_EXCL | os.O_RDWR,
                    )
                    self._fh = fd
                    return self
                except FileExistsError:
                    time.sleep(0.01)
            self._fh = None
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


def reset_seq(session_id: uuid.UUID | str) -> None:
    """Run boundary marker — does not zero durable seq."""
    return


def begin_run_events(session_id: uuid.UUID | str) -> None:
    reset_seq(session_id)


def load_recent_events(
    session_id: uuid.UUID | str,
    *,
    after_seq: int = 0,
    limit: int = 40,
) -> list[dict[str, Any]]:
    sid = str(session_id)
    path = _spill_path(sid)
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if not isinstance(obj, dict):
                    continue
                seq = int(obj.get("seq") or 0)
                if seq <= after_seq:
                    continue
                out.append(obj)
        if limit > 0 and len(out) > limit:
            out = out[-limit:]
    except Exception as e:
        logger.debug("load_recent_events skip: %s", e)
    return out


def _atomic_emit(session_id: str, event: str, msg_base: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Under file lock: allocate seq + append spill. Returns (seq, full_msg)."""
    path = _spill_path(session_id)
    lock_path = path.with_suffix(".lock")
    with _SpillFileLock(lock_path):
        # Re-read max under lock (other processes may have written)
        max_s = _max_seq_from_file(path)
        with _mem_lock:
            mem = _seq.get(session_id, 0)
            n = max(max_s, mem) + 1
            _seq[session_id] = n
        msg = dict(msg_base)
        msg["seq"] = n
        msg["event_id"] = f"{session_id}:{n}:{uuid.uuid4().hex[:8]}"
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")
            # trim under same lock
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
                if len(lines) > _MAX_SPILL_LINES:
                    path.write_text(
                        "\n".join(lines[-_MAX_SPILL_LINES:]) + "\n",
                        encoding="utf-8",
                    )
            except Exception:
                pass
        except Exception as e:
            logger.debug("spill append skip: %s", e)
        return n, msg


async def emit_run_event(
    ws_manager: Any,
    session_id: uuid.UUID | str,
    event: str,
    *,
    detail: str | None = None,
    run_id: str | None = None,
    payload: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    generation: int | None = None,
) -> int:
    sid = str(session_id)
    now = time.time()
    body = dict(payload or data or {})
    if run_id:
        body.setdefault("run_id", run_id)
    if generation is not None:
        body.setdefault("generation", int(generation))
        body.setdefault("run_generation", int(generation))
    body.setdefault("session_id", sid)

    msg_base: dict[str, Any] = {
        "type": "run_event",
        "session_id": sid,
        "event": event,
        "topic": event,
        "timestamp": now,
        "ts": datetime.now(timezone.utc).isoformat(),
        "data": body,
        "payload": body,
    }
    if detail is not None:
        msg_base["detail"] = str(detail)[:500]
    if run_id:
        msg_base["run_id"] = str(run_id)
    if generation is not None:
        msg_base["generation"] = int(generation)
        msg_base["run_generation"] = int(generation)

    seq, msg = _atomic_emit(sid, event, msg_base)

    if ws_manager is None:
        return seq
    try:
        sid_u = session_id if isinstance(session_id, uuid.UUID) else uuid.UUID(str(session_id))
        await ws_manager.broadcast(sid_u, msg)
    except Exception as e:
        logger.debug("emit_run_event skip: %s", e)
    return seq
