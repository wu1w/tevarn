"""LLM admission controller."""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from backend.kernel.llm_priority import (
    LlmAdmissionRejected,
    LlmLease,
    LlmLeaseRequest,
    _is_owner_req,
)
from backend.kernel.llm_quota import DailyTokenQuota

logger = logging.getLogger(__name__)

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
