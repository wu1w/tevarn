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
from typing import Any  # noqa: F401 — used in _rust return

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


def _rust() -> Any | None:
    try:
        from backend.kernel import get_kernel

        k = get_kernel()
        if hasattr(k, "_call"):
            return k
    except Exception:
        pass
    return None


def map_kernel_kind(kind: str) -> str | None:
    if kind in _KIND_MAP:
        return _KIND_MAP[kind]
    if kind.startswith("inbox_"):
        return f"job.{kind[6:]}"
    if kind.startswith("process_"):
        return f"process.{kind[8:]}"
    return None


def current_seq() -> int:
    k = _rust()
    if k is not None:
        try:
            r = k._call("domain_seq") or {}
            return int(r.get("seq") or 0)
        except Exception:
            pass
    return _seq


def recent_events(
    *,
    limit: int = 50,
    prefix: str | None = None,
    since_ts: float | None = None,
    after_seq: int | None = None,
) -> list[dict[str, Any]]:
    # R4: prefer Rust domain bus
    k = _rust()
    if k is not None:
        try:
            params: dict[str, Any] = {"limit": int(limit)}
            if prefix:
                params["prefix"] = prefix
            if since_ts is not None:
                params["since_ts"] = float(since_ts)
            if after_seq is not None:
                params["after_seq"] = int(after_seq)
            r = k._call("domain_recent", params) or {}
            evs = r.get("events") or []
            if isinstance(evs, list):
                return list(evs)
        except Exception as e:
            logger.debug("domain_recent rust skip: %s", e)
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
    # R4: prefer Rust domain bus as authority
    k = _rust()
    if k is not None:
        try:
            r = k._call(
                "domain_publish",
                {"topic": topic, "payload": payload or {}},
            ) or {}
            if r.get("seq") is not None:
                _seq = max(_seq, int(r.get("seq") or 0))
                evt = {
                    "type": "domain_event",
                    "topic": str(r.get("topic") or topic),
                    "ts": float(r.get("ts") or time.time()),
                    "seq": int(r.get("seq") or _seq),
                    "data": r.get("payload")
                    if isinstance(r.get("payload"), dict)
                    else dict(payload or {}),
                    "payload": r.get("payload")
                    if isinstance(r.get("payload"), dict)
                    else dict(payload or {}),
                }
                _RECENT.append(evt)
                _fanout_queues(evt)
                return evt
        except Exception as e:
            logger.debug("domain_publish rust skip: %s", e)
    _seq += 1
    evt = {
        "type": "domain_event",
        "topic": topic,
        "ts": time.time(),
        "seq": _seq,
        "data": dict(payload or {}),
        "payload": dict(payload or {}),
    }
    _RECENT.append(evt)
    _fanout_queues(evt)
    return evt


def publish_sync(topic: str, payload: dict[str, Any] | None = None) -> None:
    """同步入口：写缓冲 + 队列 fanout + 调度 async event_bus。"""
    payload = dict(payload or {})
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is None:
        # 非事件循环线程：维持原同步行为
        _record(topic, payload)
        return
    # audit-fix: 事件循环线程内 _record 会走同步 _call 阻塞 loop；
    # 改走 to_thread + create_task，保持事件最终落盘/广播
    async def _go() -> None:
        try:
            evt = await asyncio.to_thread(_record, topic, payload)
            from backend.core.event_bus import event_bus

            await event_bus.publish(
                topic,
                {**payload, "_domain": True, "ts": evt["ts"], "seq": evt["seq"]},
            )
        except Exception as e:
            logger.debug("domain_events publish_sync: %s", e)

    try:
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
    # audit-fix: _record 内部走同步 kernel _call，放到线程避免阻塞事件循环
    evt = await asyncio.to_thread(_record, topic, payload)
    from backend.core.event_bus import event_bus

    await event_bus.publish(
        topic, {**payload, "_domain": True, "ts": evt["ts"], "seq": evt["seq"]}
    )
