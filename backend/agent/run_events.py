"""Unified run event emission with monotonic seq + durable spill.

Wire format (single protocol — both FE styles filled):
  {
    type: "run_event",
    session_id, event, topic,   # event == topic (canonical)
    seq, run_id?, generation?,
    timestamp,                  # unix float
    ts,                         # ISO-ish for legacy
    detail?, data?, payload?
  }

Spill: ~/.tevarn/run_events/{session}.jsonl for reconnect replay (same-host).
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
_MAX_SPILL_LINES = 500


def _next_seq(session_id: str) -> int:
    with _lock:
        n = _seq.get(session_id, 0) + 1
        _seq[session_id] = n
        return n


def reset_seq(session_id: uuid.UUID | str) -> None:
    with _lock:
        _seq.pop(str(session_id), None)


def _spill_dir() -> Path:
    try:
        p = Path.home() / ".tevarn" / "run_events"
        p.mkdir(parents=True, exist_ok=True)
        return p
    except Exception:
        p = Path.cwd() / ".tevarn" / "run_events"
        p.mkdir(parents=True, exist_ok=True)
        return p


def _spill_path(session_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)[:80]
    return _spill_dir() / f"{safe}.jsonl"


def _append_spill(session_id: str, msg: dict[str, Any]) -> None:
    try:
        path = _spill_path(session_id)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(msg, ensure_ascii=False, default=str) + "\n")
        # soft trim
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
        logger.debug("run_event spill skip: %s", e)


def load_recent_events(
    session_id: uuid.UUID | str,
    *,
    after_seq: int = 0,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Load spilled events for reconnect replay (seq > after_seq)."""
    path = _spill_path(str(session_id))
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue
            try:
                seq = int(obj.get("seq") or 0)
            except (TypeError, ValueError):
                seq = 0
            if seq <= after_seq:
                continue
            out.append(obj)
        if limit and len(out) > limit:
            out = out[-limit:]
    except Exception as e:
        logger.debug("load_recent_events skip: %s", e)
    return out


def clear_spill(session_id: uuid.UUID | str) -> None:
    try:
        path = _spill_path(str(session_id))
        if path.is_file():
            path.unlink()
    except Exception:
        pass
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
    """Broadcast a unified run_event; returns seq (0 if skipped)."""
    sid = str(session_id)
    event_name = (event or "").strip() or "run.unknown"
    seq = _next_seq(sid)
    now = time.time()
    ts_iso = datetime.now(timezone.utc).isoformat()

    # Merge data/payload — both keys always present for FE dual-style handlers
    body: dict[str, Any] = {}
    if data:
        body.update(data)
    if payload:
        body.update(payload)
    if detail and "detail" not in body:
        body["detail"] = detail[:500]
    if run_id:
        body.setdefault("run_id", run_id)

    msg: dict[str, Any] = {
        "type": "run_event",
        "session_id": sid,
        # canonical + legacy
        "event": event_name,
        "topic": event_name,
        "seq": seq,
        "timestamp": now,
        "ts": ts_iso,
        "data": body,
        "payload": body,
    }
    if detail:
        msg["detail"] = detail[:500]
    if run_id:
        msg["run_id"] = run_id
    if generation is not None:
        msg["generation"] = generation
        msg["run_generation"] = generation

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
