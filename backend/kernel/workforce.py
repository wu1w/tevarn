"""PLAN 阶段 0.6：workforce 装配与日报聚合。

装配关系：kernel（审计链）→ InboxService + IdentityRegistry（0.4.2 已有）
→ WorkforceDispatcher（唤醒执行）。lifespan 启动时 init + 拉起 dispatcher。

日报（「你不在的这段时间」）：聚合时间窗内的工单统计、各身份产出、
token 消耗、拦截/提权——首页不再面对空白对话框。
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_inbox_singleton: Any | None = None
_dispatcher_singleton: Any | None = None


def init_workforce(kernel: Any, session_factory: Any, settings: Any) -> tuple[Any, Any]:
    """装配 inbox + dispatcher（幂等）。返回 (inbox, dispatcher)。"""
    global _inbox_singleton, _dispatcher_singleton
    if _inbox_singleton is None:
        from backend.kernel.inbox import InboxService

        _inbox_singleton = InboxService(
            kernel,
            session_factory,
            max_pending=int(getattr(settings, "agent_inbox_max_pending", 200)),
        )
    if _dispatcher_singleton is None and kernel.identity_registry is not None:
        from backend.kernel.dispatcher import WorkforceDispatcher

        _dispatcher_singleton = WorkforceDispatcher(
            kernel,
            _inbox_singleton,
            kernel.identity_registry,
            session_factory,
            poll_seconds=float(getattr(settings, "agent_dispatcher_poll_seconds", 10)),
            item_timeout=float(getattr(settings, "agent_inbox_item_timeout", 600)),
        )
    return _inbox_singleton, _dispatcher_singleton


def get_workforce_inbox() -> Any | None:
    return _inbox_singleton


def get_workforce_dispatcher() -> Any | None:
    return _dispatcher_singleton


def reset_workforce_for_tests() -> None:
    global _inbox_singleton, _dispatcher_singleton
    _inbox_singleton = None
    _dispatcher_singleton = None


async def build_daily_report(kernel: Any, inbox: Any, *, hours: int = 24) -> dict[str, Any]:
    """workforce 工作汇报（日报数据）。

    「你不在的这段时间发生了什么」——不问用户翻日志，直接给答案。
    """
    since = time.time() - hours * 3600
    stats = await inbox.stats(since_ts=since)
    recent_done = await inbox.list_items(status="done", limit=20, since_ts=since)
    recent_failed = await inbox.list_items(status="failed", limit=10, since_ts=since)

    # 按身份聚合产出
    by_identity: dict[str, dict[str, Any]] = {}
    for item in recent_done:
        key = str(item.identity_id)
        entry = by_identity.setdefault(key, {"done": 0, "latest_results": []})
        entry["done"] += 1
        if len(entry["latest_results"]) < 3 and item.result:
            entry["latest_results"].append(item.result[:500])
    for item in recent_failed:
        key = str(item.identity_id)
        entry = by_identity.setdefault(key, {"done": 0, "latest_results": [], "failed": 0})
        entry["failed"] = entry.get("failed", 0) + 1

    # 时间窗内 kernel 事件（内存缓冲近因 + 类型统计）
    events = kernel.events(limit=1000)
    window_events = [e for e in events if e.ts >= since]
    kind_counts: dict[str, int] = {}
    denials = 0
    for e in window_events:
        kind_counts[e.kind] = kind_counts.get(e.kind, 0) + 1
        if e.kind == "mediation" and e.detail.get("allowed") is False:
            denials += 1

    pending_escalations = len(kernel.list_escalations(status="pending"))

    return {
        "hours": hours,
        "since_ts": since,
        "inbox": {
            "stats": stats,
            "total": sum(stats.values()),
            "recent_done": [
                {
                    "id": str(i.id),
                    "identity_id": str(i.identity_id),
                    "source": i.source,
                    "instruction": i.instruction[:200],
                    "result": (i.result or "")[:500],
                    "finished_at": i.finished_at,
                }
                for i in recent_done[:10]
            ],
            "recent_failed": [
                {
                    "id": str(i.id),
                    "identity_id": str(i.identity_id),
                    "instruction": i.instruction[:200],
                    "error": (i.error or "")[:300],
                }
                for i in recent_failed[:5]
            ],
        },
        "by_identity": by_identity,
        "kernel": {
            "event_kinds": kind_counts,
            "mediation_denials": denials,
            "pending_escalations": pending_escalations,
        },
    }
