"""领域事件：产品级 kind → 进程内 event_bus + 近期缓冲 + cursor。

与 Kernel 审计哈希链并存：
- 审计链：不可抵赖、哈希连续
- 领域事件：UI/CLI 订阅（job.* / approval.* / process.* …）

Kernel._emit 同步调用 publish_from_kernel_event（不 await 失败）。
续订：since_ts / after_seq（单调序号）。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any

logger = logging.getLogger(__name__)

_RECENT: deque[dict[str, Any]] = deque(maxlen=500)
_queues: list[asyncio.Queue] = []
_seq: int = 0

_KIND_MAP: dict[str, str] = {
    "inbox_enqueued": "job.enqueued",
    "inbox_claimed": "job.claimed",
    "inbox_done": "job.done",
    "inbox_dead": "job.dead",
    "inbox_retry": "job.retry",
    "inbox_cancelled": "job.cancelled",
    "inbox_requeued": "job.requeued",
    "inbox_discarded": "job.discarded",
    "inbox_reclaimed": "job.reclaimed",
    "inbox_dropped": "job.dropped",
    "inbox_overflow_drop": "job.overflow",
    "process_created": "process.created",
    "process_ended": "process.ended",
    "process_suspended": "process.suspended",
    "process_resumed": "process.resumed",
    "policy.decision": "policy.decision",
    "mediation": "policy.mediation",
    "escalate": "approval.pending",
    "escalation_requested": "approval.pending",
    "escalation_approved": "approval.resolved",
    "escalation_denied": "approval.resolved",
    # LLM 公平调度（publish_sync 直写 topic 时不经 map；此处兼容 kernel._emit）
    "scheduler.queued": "scheduler.queued",
    "scheduler.granted": "scheduler.granted",
    "scheduler.released": "scheduler.released",
    "scheduler.rejected": "scheduler.rejected",
}


def map_kernel_kind(kind: str) -> str | None:
    if kind in _KIND_MAP:
        return _KIND_MAP[kind]
    if kind.startswith("inbox_"):
        return f"job.{kind[6:]}"
    if kind.startswith("process_"):
        return f"process.{kind[8:]}"
    return None


def current_seq() -> int:
    return _seq


def recent_events(
    *,
    limit: int = 50,
    prefix: str | None = None,
    since_ts: float | None = None,
    after_seq: int | None = None,
) -> list[dict[str, Any]]:
    items = list(_RECENT)
    if since_ts is not None:
        try:
            st = float(since_ts)
            items = [e for e in items if float(e.get("ts") or 0) > st]
        except (TypeError, ValueError):
            pass
    if after_seq is not None:
        try:
            asq = int(after_seq)
            items = [e for e in items if int(e.get("seq") or 0) > asq]
        except (TypeError, ValueError):
            pass
    if prefix:
        items = [e for e in items if str(e.get("topic", "")).startswith(prefix)]
    return items[-limit:]


def subscribe_queue(*, maxsize: int = 256) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
    _queues.append(q)
    return q


def unsubscribe_queue(q: asyncio.Queue) -> None:
    try:
        _queues.remove(q)
    except ValueError:
        pass


def _fanout_queues(evt: dict[str, Any]) -> None:
    dead: list[asyncio.Queue] = []
    for q in list(_queues):
        try:
            q.put_nowait(evt)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
            except Exception:
                pass
            try:
                q.put_nowait(evt)
            except Exception:
                dead.append(q)
        except Exception:
            dead.append(q)
    for q in dead:
        unsubscribe_queue(q)


def _record(topic: str, payload: dict[str, Any]) -> dict[str, Any]:
    global _seq
    _seq += 1
    evt = {
        "type": "domain_event",
        "topic": topic,
        "ts": time.time(),
        "seq": _seq,
        "data": dict(payload or {}),
    }
    _RECENT.append(evt)
    _fanout_queues(evt)
    return evt


def publish_sync(topic: str, payload: dict[str, Any] | None = None) -> None:
    """同步入口：写缓冲 + 队列 fanout + 调度 async event_bus。"""
    payload = dict(payload or {})
    evt = _record(topic, payload)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    try:
        from backend.core.event_bus import event_bus

        async def _go() -> None:
            await event_bus.publish(
                topic,
                {**payload, "_domain": True, "ts": evt["ts"], "seq": evt["seq"]},
            )

        loop.create_task(_go())
    except Exception as e:
        logger.debug("domain_events publish_sync: %s", e)


def publish_from_kernel_event(
    kind: str,
    process_id: str,
    detail: dict[str, Any] | None = None,
) -> str | None:
    """由 Kernel._emit 调用。返回领域 topic 或 None。"""
    topic = map_kernel_kind(kind)
    if not topic:
        return None
    detail = dict(detail or {})
    payload = {
        "kernel_kind": kind,
        "process_id": process_id,
        **detail,
    }
    if "item_id" in detail and "job_id" not in payload:
        payload["job_id"] = detail.get("item_id")
    # 统一关联字段（Run 模型）
    if "inbox_item_id" in detail and "job_id" not in payload:
        payload["job_id"] = detail.get("inbox_item_id")
    if "identity_id" in detail:
        payload["identity_id"] = detail.get("identity_id")
    publish_sync(topic, payload)
    return topic


async def publish_async(topic: str, payload: dict[str, Any] | None = None) -> None:
    payload = dict(payload or {})
    evt = _record(topic, payload)
    from backend.core.event_bus import event_bus

    await event_bus.publish(
        topic, {**payload, "_domain": True, "ts": evt["ts"], "seq": evt["seq"]}
    )
