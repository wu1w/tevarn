"""LLM 资源公平调度：全局 in-flight 槽位 + 优先级队列 + 日配额。

与 AgentScheduler（任务优先级堆）分离：本模块只做 **模型 HTTP 准入**。
挂接点：agent/phases/llm_round.acquire → finally release。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)


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


class DailyTokenQuota:
    """进程内日配额记账（单 worker alpha 足够；多 worker 后续可 Redis）。"""

    def __init__(self) -> None:
        self._day: str = ""
        self._global_used: int = 0
        self._by_identity: dict[str, int] = {}

    def _roll(self) -> None:
        day = time.strftime("%Y-%m-%d", time.localtime())
        if day != self._day:
            self._day = day
            self._global_used = 0
            self._by_identity.clear()

    def used_global(self) -> int:
        self._roll()
        return self._global_used

    def used_identity(self, identity_id: str | None) -> int:
        self._roll()
        if not identity_id:
            return 0
        return int(self._by_identity.get(str(identity_id), 0))

    def charge(self, identity_id: str | None, amount: int) -> None:
        if amount <= 0:
            return
        self._roll()
        self._global_used += int(amount)
        if identity_id:
            k = str(identity_id)
            self._by_identity[k] = int(self._by_identity.get(k, 0)) + int(amount)

    def would_exceed(
        self,
        identity_id: str | None,
        *,
        global_limit: int,
        per_identity_limit: int,
        estimated: int = 0,
    ) -> str | None:
        """返回拒绝原因；None = 可通过。limit<=0 表示不限制。"""
        self._roll()
        est = max(0, int(estimated or 0))
        # 已达硬顶即拒；或预估会顶穿
        if global_limit > 0 and (
            self._global_used >= global_limit
            or self._global_used + est > global_limit
        ):
            return "global_daily_quota"
        if per_identity_limit > 0 and identity_id:
            used_i = self.used_identity(identity_id)
            if used_i >= per_identity_limit or used_i + est > per_identity_limit:
                return "identity_daily_quota"
        return None

    def snapshot(
        self, *, global_limit: int, per_identity_limit: int
    ) -> dict[str, Any]:
        self._roll()
        by = [
            {
                "identity_id": iid,
                "used": used,
                "limit": per_identity_limit if per_identity_limit > 0 else None,
            }
            for iid, used in sorted(self._by_identity.items(), key=lambda x: -x[1])
        ]
        return {
            "day": self._day,
            "global_used_today": self._global_used,
            "global_limit": global_limit if global_limit > 0 else None,
            "per_identity_limit": per_identity_limit if per_identity_limit > 0 else None,
            "by_identity": by,
        }


class LlmAdmissionController:
    """全局 LLM 准入：槽位 · 排队 · 主人预留 · 加权公平 · 日配额。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._cv = asyncio.Condition(self._lock)
        self._in_flight: dict[str, LlmLease] = {}
        self._queued: dict[str, LlmLeaseRequest] = {}
        self._waiters: dict[str, asyncio.Future[LlmLease | BaseException]] = {}
        self.quota = DailyTokenQuota()
        self._lease_timeout = 600.0  # 持有过久强制回收
        self._grant_timeout = 300.0  # 排队等待上限

    # ── 配置（每次读 settings，支持热改 env/runtime）──────────

    def _cfg(self) -> dict[str, Any]:
        try:
            from backend.core.config import settings

            return {
                "max_in_flight": max(
                    1, int(getattr(settings, "llm_max_in_flight", 4) or 4)
                ),
                "max_per_identity": max(
                    1, int(getattr(settings, "llm_max_in_flight_per_identity", 1) or 1)
                ),
                "owner_reserve": max(
                    0, int(getattr(settings, "llm_owner_reserve_slots", 1) or 0)
                ),
                "queue_max": max(1, int(getattr(settings, "llm_queue_max", 64) or 64)),
                "fairness_wait_weight": float(
                    getattr(settings, "llm_fairness_wait_weight", 1.0) or 1.0
                ),
                "daily_global": max(
                    0, int(getattr(settings, "llm_daily_token_budget_global", 0) or 0)
                ),
                "daily_identity": max(
                    0,
                    int(getattr(settings, "llm_daily_token_budget_per_identity", 0) or 0),
                ),
            }
        except Exception:
            return {
                "max_in_flight": 4,
                "max_per_identity": 1,
                "owner_reserve": 1,
                "queue_max": 64,
                "fairness_wait_weight": 1.0,
                "daily_global": 0,
                "daily_identity": 0,
            }

    def _score(self, req: LlmLeaseRequest, cfg: dict[str, Any]) -> float:
        wait = max(0.0, time.time() - float(req.enqueued_at or time.time()))
        wait_term = float(cfg["fairness_wait_weight"]) * min(wait, 300.0) / 10.0
        id_penalty = 0.0
        if req.identity_id:
            n = sum(
                1
                for L in self._in_flight.values()
                if L.identity_id and L.identity_id == req.identity_id
            )
            if n > 0:
                id_penalty = 1000.0
        return float(int(req.priority)) + wait_term + float(req.wait_boost) - id_penalty

    def _identity_inflight(self, identity_id: str | None) -> int:
        if not identity_id:
            return 0
        return sum(
            1 for L in self._in_flight.values() if L.identity_id == str(identity_id)
        )

    def _can_grant_now(self, req: LlmLeaseRequest, cfg: dict[str, Any]) -> str | None:
        """None=可授予；否则返回 queue/reject 原因码。"""
        qerr = self.quota.would_exceed(
            req.identity_id,
            global_limit=int(cfg["daily_global"]),
            per_identity_limit=int(cfg["daily_identity"]),
            estimated=int(req.estimated_tokens or 0),
        )
        if qerr:
            return f"reject:{qerr}"

        max_if = int(cfg["max_in_flight"])
        if len(self._in_flight) >= max_if:
            return "queue:full"

        if req.identity_id and self._identity_inflight(req.identity_id) >= int(
            cfg["max_per_identity"]
        ):
            return "queue:identity"

        free = max_if - len(self._in_flight)
        owner_reserve = int(cfg["owner_reserve"])
        if owner_reserve > 0 and free <= owner_reserve and not _is_owner_req(req):
            return "queue:owner_reserve"

        return None

    def _emit(self, topic: str, payload: dict[str, Any]) -> None:
        try:
            from backend.kernel.domain_events import publish_sync

            publish_sync(topic, payload)
        except Exception as e:
            logger.debug("llm_scheduler emit %s: %s", topic, e)

    def _grant_locked(self, req: LlmLeaseRequest) -> LlmLease:
        lease = LlmLease(
            request_id=req.request_id,
            granted_at=time.time(),
            source=req.source,
            identity_id=req.identity_id,
            process_id=req.process_id,
            priority=int(req.priority),
            is_owner=_is_owner_req(req),
        )
        self._in_flight[lease.request_id] = lease
        self._queued.pop(req.request_id, None)
        self._emit(
            "scheduler.granted",
            {
                "request_id": lease.request_id,
                "source": lease.source,
                "identity_id": lease.identity_id,
                "process_id": lease.process_id,
                "priority": lease.priority,
                "wait_ms": int(max(0.0, lease.granted_at - req.enqueued_at) * 1000),
            },
        )
        return lease

    def _pick_best_queued(self, cfg: dict[str, Any]) -> LlmLeaseRequest | None:
        if not self._queued:
            return None
        # 只考虑当前可授予的请求；按 score 降序
        candidates: list[tuple[float, LlmLeaseRequest]] = []
        for req in self._queued.values():
            reason = self._can_grant_now(req, cfg)
            if reason is None:
                candidates.append((self._score(req, cfg), req))
        if not candidates:
            return None
        candidates.sort(key=lambda x: (-x[0], x[1].enqueued_at))
        return candidates[0][1]

    def _wake_best(self) -> None:
        cfg = self._cfg()
        req = self._pick_best_queued(cfg)
        if req is None:
            return
        fut = self._waiters.get(req.request_id)
        if fut is None or fut.done():
            return
        lease = self._grant_locked(req)
        fut.set_result(lease)

    async def acquire(self, req: LlmLeaseRequest | None = None, **kwargs: Any) -> LlmLease:
        """阻塞直到获槽或拒绝。调用方必须 release（或用 lease_context）。"""
        if req is None:
            req = LlmLeaseRequest(**kwargs)
        cfg = self._cfg()

        async with self._cv:
            # 即时配额拒绝
            qerr = self.quota.would_exceed(
                req.identity_id,
                global_limit=int(cfg["daily_global"]),
                per_identity_limit=int(cfg["daily_identity"]),
                estimated=int(req.estimated_tokens or 0),
            )
            if qerr:
                self._emit(
                    "scheduler.rejected",
                    {
                        "request_id": req.request_id,
                        "reason": qerr,
                        "identity_id": req.identity_id,
                        "source": req.source,
                    },
                )
                raise LlmAdmissionRejected(
                    f"LLM 日配额已用尽（{qerr}）", code=qerr
                )

            reason = self._can_grant_now(req, cfg)
            if reason is None and not self._queued:
                return self._grant_locked(req)

            if len(self._queued) >= int(cfg["queue_max"]):
                self._emit(
                    "scheduler.rejected",
                    {
                        "request_id": req.request_id,
                        "reason": "queue_full",
                        "identity_id": req.identity_id,
                        "source": req.source,
                    },
                )
                raise LlmAdmissionRejected("LLM 排队已满", code="queue_full")

            # 入队
            req.enqueued_at = time.time()
            self._queued[req.request_id] = req
            loop = asyncio.get_running_loop()
            fut: asyncio.Future[LlmLease | BaseException] = loop.create_future()
            self._waiters[req.request_id] = fut
            self._emit(
                "scheduler.queued",
                {
                    "request_id": req.request_id,
                    "source": req.source,
                    "identity_id": req.identity_id,
                    "priority": int(req.priority),
                    "queue_len": len(self._queued),
                    "reason": reason or "queued",
                },
            )
            # 若其实可立即授予（仅因有人排队），尝试调度
            self._wake_best()

        try:
            result = await asyncio.wait_for(fut, timeout=self._grant_timeout)
        except asyncio.TimeoutError:
            async with self._cv:
                self._queued.pop(req.request_id, None)
                self._waiters.pop(req.request_id, None)
            self._emit(
                "scheduler.rejected",
                {
                    "request_id": req.request_id,
                    "reason": "wait_timeout",
                    "identity_id": req.identity_id,
                },
            )
            raise LlmAdmissionRejected("等待 LLM 槽位超时", code="wait_timeout") from None
        finally:
            async with self._cv:
                self._waiters.pop(req.request_id, None)
                self._queued.pop(req.request_id, None)

        if isinstance(result, BaseException):
            raise result
        return result

    async def release(self, lease: LlmLease | None) -> None:
        if lease is None:
            return
        async with self._cv:
            gone = self._in_flight.pop(lease.request_id, None)
            if gone is None:
                return
            self._emit(
                "scheduler.released",
                {
                    "request_id": lease.request_id,
                    "source": lease.source,
                    "identity_id": lease.identity_id,
                    "held_ms": int(max(0.0, time.time() - lease.granted_at) * 1000),
                },
            )
            self._wake_best()
            self._cv.notify_all()

    def charge_quota(self, identity_id: str | None, amount: int) -> None:
        self.quota.charge(identity_id, amount)

    @asynccontextmanager
    async def lease_context(
        self, req: LlmLeaseRequest | None = None, **kwargs: Any
    ) -> AsyncIterator[LlmLease]:
        lease = await self.acquire(req, **kwargs)
        try:
            yield lease
        finally:
            await self.release(lease)

    def status(self) -> dict[str, Any]:
        cfg = self._cfg()
        now = time.time()
        in_flight = [
            {
                "request_id": L.request_id,
                "source": L.source,
                "identity_id": L.identity_id,
                "process_id": L.process_id,
                "priority": L.priority,
                "is_owner": L.is_owner,
                "held_ms": int(max(0.0, now - L.granted_at) * 1000),
            }
            for L in self._in_flight.values()
        ]
        queued = [
            {
                "request_id": r.request_id,
                "source": r.source,
                "identity_id": r.identity_id,
                "priority": int(r.priority),
                "wait_ms": int(max(0.0, now - r.enqueued_at) * 1000),
                "score": self._score(r, cfg),
            }
            for r in sorted(
                self._queued.values(),
                key=lambda x: (-self._score(x, cfg), x.enqueued_at),
            )
        ]
        return {
            "in_flight": in_flight,
            "queued": queued,
            "config": {
                "llm_max_in_flight": cfg["max_in_flight"],
                "llm_max_in_flight_per_identity": cfg["max_per_identity"],
                "llm_owner_reserve_slots": cfg["owner_reserve"],
                "llm_queue_max": cfg["queue_max"],
                "llm_fairness_wait_weight": cfg["fairness_wait_weight"],
                "llm_daily_token_budget_global": cfg["daily_global"],
                "llm_daily_token_budget_per_identity": cfg["daily_identity"],
            },
            "quota": self.quota.snapshot(
                global_limit=int(cfg["daily_global"]),
                per_identity_limit=int(cfg["daily_identity"]),
            ),
            "counts": {
                "in_flight": len(in_flight),
                "queued": len(queued),
            },
        }

    def reset_for_tests(self) -> None:
        """测试用：清空状态。"""
        self._in_flight.clear()
        self._queued.clear()
        for fut in self._waiters.values():
            if not fut.done():
                fut.cancel()
        self._waiters.clear()
        self.quota = DailyTokenQuota()


_controller: LlmAdmissionController | None = None


def get_llm_admission() -> LlmAdmissionController:
    global _controller
    if _controller is None:
        _controller = LlmAdmissionController()
    return _controller


def reset_llm_admission_for_tests() -> None:
    global _controller
    if _controller is not None:
        _controller.reset_for_tests()
    _controller = LlmAdmissionController()
