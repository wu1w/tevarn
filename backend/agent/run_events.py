"""Unified run event emission with durable monotonic seq + spill.

Wire format (single protocol — both FE styles filled):
  {
    type: "run_event",
    session_id, event, topic,   # event == topic (canonical)
    seq, run_id, generation, event_id,
    timestamp,                  # unix float
    ts,                         # ISO for legacy
    detail?, data?, payload?
  }

Seq is durable: continues from max seq in spill file across process restarts.
reset_seq() no longer zeroes counters (generation is the run boundary).
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_seq: dict[str, int] = {}
_seq_loaded: set[str] = set()
_MAX_SPILL_LINES = 500


def _spill_dir() -> Path:
    try:
        from backend.core.config import get_tevarn_home

        p = get_tevarn_home() / "run_events"
        p.mkdir(parents=True, exist_ok=True)
        return p
    except Exception:
        try:
            p = Path.home() / ".tevarn" / "run_events"
            p.mkdir(parents=True, exist_ok=True)
            return p
        except Exception:
            p = Path.cwd() / ".tevarn" / "run_events"
            p.mkdir(parents=True, exist_ok=True)
            return p


def _spill_path(session_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)[:64]
    return _spill_dir() / f"{safe}.jsonl"


def _max_seq_from_spill(session_id: str) -> int:
    path = _spill_path(session_id)
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


def _ensure_seq_loaded(session_id: str) -> None:
    """Hydrate in-memory counter from durable spill once per process."""
    if session_id in _seq_loaded:
        return
    max_s = _max_seq_from_spill(session_id)
    cur = _seq.get(session_id, 0)
    if max_s > cur:
        _seq[session_id] = max_s
    _seq_loaded.add(session_id)


def _next_seq(session_id: str) -> int:
    with _lock:
        _ensure_seq_loaded(session_id)
        n = _seq.get(session_id, 0) + 1
        _seq[session_id] = n
        return n


def reset_seq(session_id: uuid.UUID | str) -> None:
    """Run boundary marker — does NOT zero durable seq (prevents after_seq holes).

    Historical name kept for call-site compatibility.
    """
    sid = str(session_id)
    with _lock:
        _ensure_seq_loaded(sid)
        # intentionally do not pop/zero


def _append_spill(session_id: str, msg: dict[str, Any]) -> None:
    path = _spill_path(session_id)
    lock_path = path.with_suffix(".lock")
    try:
        # Cross-process lock for append + trim
        try:
            import fcntl

            with open(lock_path, "a+", encoding="utf-8") as lf:
                fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
                with open(path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(msg, ensure_ascii=False) + "\n")
                # trim
                try:
                    lines = path.read_text(encoding="utf-8").splitlines()
                    if len(lines) > _MAX_SPILL_LINES:
                        path.write_text(
                            "\n".join(lines[-_MAX_SPILL_LINES:]) + "\n",
                            encoding="utf-8",
                        )
                except Exception:
                    pass
                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
            return
        except ImportError:
            pass
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            if len(lines) > _MAX_SPILL_LINES:
                path.write_text(
                    "\n".join(lines[-_MAX_SPILL_LINES:]) + "\n", encoding="utf-8"
                )
        except Exception:
            pass
    except Exception as e:
        logger.debug("spill append skip: %s", e)


def load_recent_events(
    session_id: uuid.UUID | str,
    *,
    after_seq: int = 0,
    limit: int = 40,
) -> list[dict[str, Any]]:
    """Load spilled events for reconnect replay (seq > after_seq)."""
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


def begin_run_events(session_id: uuid.UUID | str) -> None:
    """Hydrate seq from spill at run start (no zeroing)."""
    reset_seq(session_id)


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
    seq = _next_seq(sid)
    now = time.time()
    body = dict(payload or data or {})
    # Force identity fields onto payload for consumers that only read data
    if run_id:
        body.setdefault("run_id", run_id)
    if generation is not None:
        body.setdefault("generation", int(generation))
        body.setdefault("run_generation", int(generation))
    body.setdefault("session_id", sid)

    event_id = f"{sid}:{seq}:{uuid.uuid4().hex[:8]}"
    msg: dict[str, Any] = {
        "type": "run_event",
        "session_id": sid,
        "event": event,
        "topic": event,
        "seq": seq,
        "event_id": event_id,
        "timestamp": now,
        "ts": datetime.now(timezone.utc).isoformat(),
        "data": body,
        "payload": body,
    }
    if detail is not None:
        msg["detail"] = str(detail)[:500]
    if run_id:
        msg["run_id"] = str(run_id)
    if generation is not None:
        msg["generation"] = int(generation)

    _append_spill(sid, msg)

    if ws_manager is None:
        return seq
    try:
        sid_u: uuid.UUID
        if isinstance(session_id, uuid.UUID):
            sid_u = session_id
        else:
            sid_u = uuid.UUID(str(session_id))
        await ws_manager.broadcast(sid_u, msg)
    except Exception as e:
        logger.debug("emit_run_event skip: %s", e)
    return seq
