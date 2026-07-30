"""LLM scheduler types: Priority, leases, helpers."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


class Priority(IntEnum):
    OWNER_CHAT = 100
    INTERACTIVE = 80
    WORKFORCE_HIGH = 50
    WORKFORCE_NORMAL = 30
    WORKFORCE_LOW = 10
    BACKGROUND = 5


class LlmAdmissionRejected(Exception):
    """队列满 / 日配额尽 — 调用方应 fail terminal 或返回人话错误。"""

    def __init__(self, reason: str, *, code: str = "rejected") -> None:
        super().__init__(reason)
        self.reason = reason
        self.code = code


@dataclass
class LlmLeaseRequest:
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    source: str = "chat"  # chat | workforce | cron | subagent
    identity_id: str | None = None
    session_id: str | None = None
    process_id: str | None = None
    inbox_item_id: str | None = None
    priority: Priority = Priority.OWNER_CHAT
    enqueued_at: float = field(default_factory=time.time)
    estimated_tokens: int = 0
    wait_boost: float = 0.0


@dataclass
class LlmLease:
    request_id: str
    granted_at: float
    source: str
    identity_id: str | None
    process_id: str | None
    priority: int
    is_owner: bool = False


def _is_owner_req(req: LlmLeaseRequest) -> bool:
    if req.source in ("chat", "interactive"):
        return True
    return int(req.priority) >= int(Priority.INTERACTIVE)


def map_inbox_priority(priority: int | None) -> Priority:
    try:
        p = int(priority or 0)
    except (TypeError, ValueError):
        p = 0
    if p >= 10:
        return Priority.WORKFORCE_HIGH
    if p < 0:
        return Priority.WORKFORCE_LOW
    return Priority.WORKFORCE_NORMAL


def infer_request_from_loop(loop: Any) -> LlmLeaseRequest:
    """从 NexusAgentLoop 属性推断 LLM 准入请求。"""
    meta: dict[str, Any] = {}
    opts = getattr(loop, "_kernel_process_options", None) or {}
    if isinstance(opts, dict):
        m = opts.get("meta")
        if isinstance(m, dict):
            meta = m

    workforce = bool(getattr(loop, "_workforce", False) or meta.get("workforce"))
    source = str(
        getattr(loop, "_llm_source", None)
        or meta.get("llm_source")
        or ("workforce" if workforce else "chat")
    )
    raw_pri = getattr(loop, "_llm_priority", None)
    if raw_pri is None:
        raw_pri = meta.get("llm_priority")
    if raw_pri is not None:
        try:
            priority = Priority(int(raw_pri))
        except (TypeError, ValueError):
            priority = (
                map_inbox_priority(meta.get("inbox_priority"))
                if workforce
                else Priority.OWNER_CHAT
            )
    elif workforce:
        priority = map_inbox_priority(meta.get("inbox_priority"))
    elif source in ("cron", "background"):
        priority = Priority.BACKGROUND
    elif source == "subagent":
        priority = Priority.WORKFORCE_NORMAL
    else:
        priority = Priority.OWNER_CHAT

    proc = getattr(loop, "_kernel_process", None)
    process_id = getattr(proc, "id", None) if proc is not None else None
    identity_id = (
        getattr(loop, "_identity_id", None)
        or meta.get("identity_id")
        or None
    )
    if identity_id is not None:
        identity_id = str(identity_id)

    return LlmLeaseRequest(
        source=source,
        identity_id=identity_id,
        session_id=str(getattr(loop, "_session_id", "") or "") or None,
        process_id=str(process_id) if process_id else None,
        inbox_item_id=(
            str(getattr(loop, "_inbox_item_id", None) or meta.get("inbox_item_id") or "")
            or None
        ),
        priority=priority,
    )
