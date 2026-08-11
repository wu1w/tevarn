"""Unified run event emission with monotonic seq (GPT-audit P0).

Events are broadcast on the session WebSocket as:
  { type: "run_event", event, seq, run_id?, timestamp, detail?, ... }

Consumers (FE) can buffer by seq for reconnect / late delivery.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_seq: dict[str, int] = {}


def _next_seq(session_id: str) -> int:
    with _lock:
        n = _seq.get(session_id, 0) + 1
        _seq[session_id] = n
        return n


def reset_seq(session_id: uuid.UUID | str) -> None:
    with _lock:
        _seq.pop(str(session_id), None)


async def emit_run_event(
    ws_manager: Any,
    session_id: uuid.UUID | str,
    event: str,
    *,
    detail: str | None = None,
    run_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> int:
    """Broadcast a run_event; returns seq (0 if skipped)."""
    if ws_manager is None:
        return 0
    sid = str(session_id)
    seq = _next_seq(sid)
    msg: dict[str, Any] = {
        "type": "run_event",
        "session_id": sid,
        "event": event,
        "seq": seq,
        "timestamp": time.time(),
    }
    if detail:
        msg["detail"] = detail[:500]
    if run_id:
        msg["run_id"] = run_id
    if payload:
        msg["payload"] = payload
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
    return seq
