"""Per-session control inbox: steer / queue without killing the current run.

GPT-audit P0: while an agent is running, user messages should be able to
  - steer  → inject into the live loop at the next safe boundary
  - queue  → run after the current turn finishes
  - stop   → cooperative cancel (existing path)
  - interrupt → stop current run and start a fresh one (legacy default)

This module is process-local; multi-worker deployments should route sticky
sessions or replace with a shared store.
"""
from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from enum import Enum
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


class SessionControlInbox:
    """Thread-safe steer queue + FIFO pending list for one session."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._steers: list[ControlMessage] = []
        self._pending: list[ControlMessage] = []

    def push_steer(self, content: str, *, meta: dict[str, Any] | None = None) -> None:
        text = (content or "").strip()
        if not text:
            return
        with self._lock:
            self._steers.append(
                ControlMessage(ControlKind.STEER, text, dict(meta or {}))
            )

    def push_queue(self, content: str, *, meta: dict[str, Any] | None = None) -> None:
        text = (content or "").strip()
        if not text:
            return
        with self._lock:
            self._pending.append(
                ControlMessage(ControlKind.QUEUE, text, dict(meta or {}))
            )

    def drain_steers(self) -> list[ControlMessage]:
        with self._lock:
            out = list(self._steers)
            self._steers.clear()
            return out

    def peek_pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def pop_queued(self) -> ControlMessage | None:
        with self._lock:
            if not self._pending:
                return None
            return self._pending.pop(0)

    def clear(self) -> None:
        with self._lock:
            self._steers.clear()
            self._pending.clear()


_registry: dict[str, SessionControlInbox] = {}
_reg_lock = threading.Lock()


def _key(session_id: uuid.UUID | str) -> str:
    return str(session_id)


def get_inbox(session_id: uuid.UUID | str) -> SessionControlInbox:
    k = _key(session_id)
    with _reg_lock:
        box = _registry.get(k)
        if box is None:
            box = SessionControlInbox()
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
