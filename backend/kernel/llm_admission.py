"""LLM admission controller.

P0-C: **权威实现在 Rust** ``takton_kernel::llm_admission``。
本模块：
- 生产路径：经 ``get_kernel()`` RPC 代理到 host（``llm_try_acquire`` / poll / release）
- fallback：进程内 Python 实现（host 不可用时）
"""
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


def _rust_kernel() -> Any | None:
    """R3: production must use Rust host; no silent dual controller."""
    try:
        from backend.kernel import get_kernel

        k = get_kernel()
        if hasattr(k, "_call"):
            return k
    except Exception as e:
        logger.debug("rust kernel for llm admission: %s", e)
    return None


def _prefer_rust_only() -> bool:
    import os

    # default true; set TAKTON_LLM_ALLOW_PY_FALLBACK=1 only for unit tests without host
    return os.environ.get("TAKTON_LLM_ALLOW_PY_FALLBACK", "0") not in (
        "1",
        "true",
        "True",
    )


def _sync_config_to_rust(k: Any) -> None:
    try:
        from backend.core.config import settings

        k._call(
            "llm_set_config",
            {
                "max_in_flight": max(1, int(getattr(settings, "llm_max_in_flight", 4) or 4)),
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
            },
        )
    except Exception as e:
        logger.debug("llm_set_config skip: %s", e)


def _lease_from_dict(d: dict[str, Any]) -> LlmLease:
    return LlmLease(
        request_id=str(d.get("request_id") or ""),
        granted_at=float(d.get("granted_at") or time.time()),
        source=str(d.get("source") or "chat"),
        identity_id=d.get("identity_id"),
        process_id=d.get("process_id"),
        priority=int(d.get("priority") or 0),
        is_owner=bool(d.get("is_owner")),
    )


class LlmAdmissionController:
    """全局 LLM 准入：槽位 · 排队 · 主人预留 · 加权公平 · 日配额。

    P0-C：优先委托 Rust host。
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._cv = asyncio.Condition(self._lock)
        self._in_flight: dict[str, LlmLease] = {}
        self._queued: dict[str, LlmLeaseRequest] = {}
        self._waiters: dict[str, asyncio.Future[LlmLease | BaseException]] = {}
        self.quota = DailyTokenQuota()
        self._lease_timeout = 600.0
        self._grant_timeout = 300.0
        self._rust_config_pushed = False

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

    def _emit(self, topic: str, payload: dict[str, Any]) -> None:
        try:
            from backend.kernel.domain_events import publish_sync

            publish_sync(topic, payload)
        except Exception as e:
            logger.debug("llm_scheduler emit %s: %s", topic, e)

    async def acquire(self, req: LlmLeaseRequest | None = None, **kwargs: Any) -> LlmLease:
        if req is None:
            req = LlmLeaseRequest(**kwargs)

        k = _rust_kernel()
        if k is not None:
            if not self._rust_config_pushed:
                _sync_config_to_rust(k)
                self._rust_config_pushed = True
            return await self._acquire_rust(k, req)

        if _prefer_rust_only():
            logger.error(
                "LLM admission: Rust host unavailable and Py fallback disabled "
                "(set TAKTON_LLM_ALLOW_PY_FALLBACK=1 only for offline tests)"
            )
            raise LlmAdmissionRejected(
                "rust kernel host required for llm admission"
            )
        return await self._acquire_python(req)

    async def _acquire_rust(self, k: Any, req: LlmLeaseRequest) -> LlmLease:
        params = {
            "request_id": req.request_id,
            "source": req.source,
            "identity_id": req.identity_id,
            "session_id": req.session_id,
            "process_id": req.process_id,
            "inbox_item_id": req.inbox_item_id,
            "priority": int(req.priority),
            "estimated_tokens": int(req.estimated_tokens or 0),
            "wait_boost": float(req.wait_boost or 0),
        }
        rid = str(req.request_id or "")
        granted = False
        try:
            # audit-fix: async 上下文走 _acall，避免阻塞事件循环
            r = await k._acall("llm_try_acquire", params) or {}
            status = r.get("status")
            rid = str(r.get("request_id") or req.request_id or rid)
            if status == "granted":
                lease = _lease_from_dict(r.get("lease") or {})
                granted = True
                self._emit(
                    "scheduler.granted",
                    {
                        "request_id": lease.request_id,
                        "source": lease.source,
                        "identity_id": lease.identity_id,
                        "process_id": lease.process_id,
                        "priority": lease.priority,
                        "backend": "rust",
                    },
                )
                return lease
            if status == "rejected":
                self._emit(
                    "scheduler.rejected",
                    {
                        "request_id": r.get("request_id"),
                        "reason": r.get("code") or r.get("reason"),
                        "identity_id": req.identity_id,
                        "source": req.source,
                        "backend": "rust",
                    },
                )
                raise LlmAdmissionRejected(
                    str(r.get("reason") or "rejected"),
                    code=str(r.get("code") or "rejected"),
                )

            # queued — poll
            rid = str(r.get("request_id") or req.request_id)
            self._emit(
                "scheduler.queued",
                {
                    "request_id": rid,
                    "source": req.source,
                    "identity_id": req.identity_id,
                    "priority": int(req.priority),
                    "queue_len": r.get("queue_len"),
                    "reason": r.get("reason"),
                    "backend": "rust",
                },
            )
            deadline = time.time() + self._grant_timeout
            while time.time() < deadline:
                await asyncio.sleep(0.05)
                polled = await k._acall("llm_poll", {"request_id": rid}) or {}
                st = polled.get("status")
                if st == "granted":
                    lease = _lease_from_dict(polled.get("lease") or {})
                    granted = True
                    self._emit(
                        "scheduler.granted",
                        {
                            "request_id": lease.request_id,
                            "source": lease.source,
                            "backend": "rust",
                        },
                    )
                    return lease
                if st == "rejected":
                    raise LlmAdmissionRejected(
                        str(polled.get("reason") or "rejected"),
                        code=str(polled.get("code") or "rejected"),
                    )
            raise LlmAdmissionRejected("等待 LLM 槽位超时", code="wait_timeout")
        finally:
            # 超时 / CancelledError / 拒绝路径：必须 cancel_wait，
            # 否则 wake_best 已 grant 的 in_flight 会永久占槽。
            # shield：外层 task 被 cancel 时 finally 内 await 仍会被打断，导致清槽失败。
            if not granted and rid:
                try:
                    await asyncio.shield(
                        k._acall("llm_cancel_wait", {"request_id": rid})
                    )
                except Exception:
                    try:
                        # 最后手段：同步 RPC，避免 cancel 路径漏槽
                        if hasattr(k, "_call"):
                            k._call("llm_cancel_wait", {"request_id": rid})
                    except Exception:
                        pass

    def _score_req(self, req: LlmLeaseRequest, cfg: dict[str, Any]) -> float:
        wait = max(0.0, time.time() - float(req.enqueued_at or time.time()))
        wait_term = float(cfg["fairness_wait_weight"]) * min(wait, 300.0) / 10.0
        return float(int(req.priority)) + float(req.wait_boost or 0) + wait_term

    def _block_reason_locked(
        self, req: LlmLeaseRequest, cfg: dict[str, Any]
    ) -> str | None:
        """None = can grant now. Must hold self._lock / _cv."""
        if len(self._in_flight) >= int(cfg["max_in_flight"]):
            return "full"
        if req.identity_id:
            n = sum(
                1
                for L in self._in_flight.values()
                if L.identity_id == req.identity_id
            )
            if n >= int(cfg["max_per_identity"]):
                return "per_identity"
        free = int(cfg["max_in_flight"]) - len(self._in_flight)
        if (
            int(cfg["owner_reserve"]) > 0
            and free <= int(cfg["owner_reserve"])
            and not _is_owner_req(req)
        ):
            return "owner_reserve"
        return None

    def _make_lease(self, req: LlmLeaseRequest) -> LlmLease:
        return LlmLease(
            request_id=req.request_id,
            granted_at=time.time(),
            source=req.source,
            identity_id=req.identity_id,
            process_id=req.process_id,
            priority=int(req.priority),
            is_owner=_is_owner_req(req),
        )

    def _wake_best_locked(self) -> None:
        """从队列选出可授权请求并 set_result Future。Must hold _cv。"""
        cfg = self._cfg()
        while self._queued and len(self._in_flight) < int(cfg["max_in_flight"]):
            candidates: list[tuple[float, float, str, LlmLeaseRequest]] = []
            for rid, req in self._queued.items():
                if self._block_reason_locked(req, cfg) is not None:
                    continue
                candidates.append(
                    (
                        self._score_req(req, cfg),
                        -float(req.enqueued_at or 0),
                        rid,
                        req,
                    )
                )
            if not candidates:
                break
            candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
            _sc, _enq, rid, req = candidates[0]
            self._queued.pop(rid, None)
            lease = self._make_lease(req)
            self._in_flight[lease.request_id] = lease
            fut = self._waiters.get(rid)
            if fut is not None and not fut.done():
                fut.set_result(lease)

    async def _acquire_python(self, req: LlmLeaseRequest) -> LlmLease:
        """In-process fallback（host 不可用时）。"""
        cfg = self._cfg()
        async with self._cv:
            qerr = self.quota.would_exceed(
                req.identity_id,
                global_limit=int(cfg["daily_global"]),
                per_identity_limit=int(cfg["daily_identity"]),
                estimated=int(req.estimated_tokens or 0),
            )
            if qerr:
                raise LlmAdmissionRejected(f"LLM 日配额已用尽（{qerr}）", code=qerr)

            # 队列为空且可立即授权
            if not self._queued and self._block_reason_locked(req, cfg) is None:
                lease = self._make_lease(req)
                self._in_flight[lease.request_id] = lease
                return lease

            if len(self._queued) >= int(cfg["queue_max"]):
                raise LlmAdmissionRejected("LLM 排队已满", code="queue_full")

            req.enqueued_at = time.time()
            self._queued[req.request_id] = req
            loop = asyncio.get_running_loop()
            fut: asyncio.Future[LlmLease | BaseException] = loop.create_future()
            self._waiters[req.request_id] = fut
            # 若此刻有空位（例如仅 owner_reserve 挡了别人），尝试唤醒
            self._wake_best_locked()

        try:
            result = await asyncio.wait_for(fut, timeout=self._grant_timeout)
        except asyncio.TimeoutError:
            async with self._cv:
                self._queued.pop(req.request_id, None)
                self._waiters.pop(req.request_id, None)
                # 若 wake 已 grant 进 in_flight 但 future 未取走，一并释放
                self._in_flight.pop(req.request_id, None)
                self._wake_best_locked()
                self._cv.notify_all()
            raise LlmAdmissionRejected("等待 LLM 槽位超时", code="wait_timeout") from None
        except asyncio.CancelledError:
            async with self._cv:
                self._queued.pop(req.request_id, None)
                self._waiters.pop(req.request_id, None)
                self._in_flight.pop(req.request_id, None)
                self._wake_best_locked()
                self._cv.notify_all()
            raise
        finally:
            async with self._cv:
                self._waiters.pop(req.request_id, None)
                # 仅清排队；已成功 grant 的 in_flight 由 release() 负责
                if req.request_id not in self._in_flight:
                    self._queued.pop(req.request_id, None)

        if isinstance(result, BaseException):
            raise result
        return result

    async def release(self, lease: LlmLease | None) -> None:
        if lease is None:
            return
        k = _rust_kernel()
        if k is not None:
            try:
                # audit-fix: async 上下文走 _acall，避免阻塞事件循环
                await k._acall("llm_release", {"request_id": lease.request_id})
                self._emit(
                    "scheduler.released",
                    {
                        "request_id": lease.request_id,
                        "source": lease.source,
                        "backend": "rust",
                    },
                )
                return
            except Exception as e:
                logger.debug("rust llm_release: %s", e)
        async with self._cv:
            self._in_flight.pop(lease.request_id, None)
            self._wake_best_locked()
            self._cv.notify_all()

    async def release_by_process(self, process_id: str | None) -> int:
        """进程结束 / 用户 stop 时回收该 process 全部 LLM 租约与排队。"""
        pid = str(process_id or "").strip()
        if not pid:
            return 0
        k = _rust_kernel()
        if k is not None:
            try:
                r = await k._acall(
                    "llm_release_by_process", {"process_id": pid}
                ) or {}
                n = int(r.get("released") or 0)
                if n:
                    self._emit(
                        "scheduler.released_by_process",
                        {"process_id": pid, "count": n, "backend": "rust"},
                    )
                return n
            except Exception as e:
                logger.debug("rust llm_release_by_process: %s", e)
        async with self._cv:
            n = 0
            for rid, lease in list(self._in_flight.items()):
                if str(lease.process_id or "") == pid:
                    self._in_flight.pop(rid, None)
                    n += 1
            for rid, req in list(self._queued.items()):
                if str(req.process_id or "") == pid:
                    self._queued.pop(rid, None)
                    fut = self._waiters.pop(rid, None)
                    if fut is not None and not fut.done():
                        fut.cancel()
                    n += 1
            if n:
                self._wake_best_locked()
                self._cv.notify_all()
            return n

    async def reclaim(
        self,
        *,
        null_pid_max_hold_secs: float = 120.0,
        max_hold_secs: float = 600.0,
        force: bool = False,
    ) -> dict[str, Any]:
        """回收孤儿/过期租约；force=True 清空全部槽位（运维自救）。"""
        k = _rust_kernel()
        if k is not None:
            try:
                if force:
                    r = await k._acall("llm_force_clear") or {}
                    self._emit(
                        "scheduler.force_cleared",
                        {"cleared": r.get("cleared"), "backend": "rust"},
                    )
                    return dict(r) if isinstance(r, dict) else {"cleared": r}
                r = await k._acall(
                    "llm_reclaim",
                    {
                        "null_pid_max_hold_secs": float(null_pid_max_hold_secs),
                        "max_hold_secs": float(max_hold_secs),
                    },
                ) or {}
                if int(r.get("reclaimed") or 0) > 0:
                    self._emit(
                        "scheduler.reclaimed",
                        {
                            "reclaimed": r.get("reclaimed"),
                            "backend": "rust",
                        },
                    )
                return dict(r) if isinstance(r, dict) else {"reclaimed": r}
            except Exception as e:
                logger.debug("rust llm_reclaim: %s", e)
        # Python fallback
        async with self._cv:
            if force:
                n = len(self._in_flight) + len(self._queued)
                self._in_flight.clear()
                self._queued.clear()
                for fut in self._waiters.values():
                    if not fut.done():
                        fut.cancel()
                self._waiters.clear()
                self._cv.notify_all()
                return {"cleared": n, "backend": "python"}
            now = time.time()
            n = 0
            for rid, lease in list(self._in_flight.items()):
                # 不可用 `granted_at or now`：0.0 会被当成缺省
                try:
                    ga = float(lease.granted_at)
                except (TypeError, ValueError):
                    ga = now
                held = max(0.0, now - ga)
                pid = str(lease.process_id or "").strip()
                drop = held > float(max_hold_secs)
                if not pid and held > float(null_pid_max_hold_secs):
                    drop = True
                if drop:
                    self._in_flight.pop(rid, None)
                    n += 1
            if n:
                self._wake_best_locked()
                self._cv.notify_all()
            return {
                "reclaimed": n,
                "backend": "python",
                "status": self.status(),
            }

    def charge_quota(self, identity_id: str | None, amount: int) -> None:
        k = _rust_kernel()
        if k is not None:
            try:
                k._call(
                    "llm_charge_quota",
                    {"identity_id": identity_id, "amount": int(amount)},
                )
                return
            except Exception:
                pass
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
        k = _rust_kernel()
        if k is not None:
            try:
                if not self._rust_config_pushed:
                    _sync_config_to_rust(k)
                    self._rust_config_pushed = True
                return k._call("llm_status") or {"backend": "rust", "error": "empty"}
            except Exception as e:
                logger.debug("llm_status rust: %s", e)
        cfg = self._cfg()
        return {
            "backend": "python",
            "in_flight": [
                {
                    "request_id": L.request_id,
                    "source": L.source,
                    "identity_id": L.identity_id,
                    "priority": L.priority,
                }
                for L in self._in_flight.values()
            ],
            "queued": [
                {
                    "request_id": r.request_id,
                    "source": r.source,
                    "identity_id": r.identity_id,
                    "priority": int(r.priority),
                }
                for r in self._queued.values()
            ],
            "config": {
                "llm_max_in_flight": cfg["max_in_flight"],
                "llm_max_in_flight_per_identity": cfg["max_per_identity"],
                "llm_owner_reserve_slots": cfg["owner_reserve"],
                "llm_fairness_wait_weight": cfg["fairness_wait_weight"],
            },
            "quota": self.quota.snapshot(
                global_limit=int(cfg["daily_global"]),
                per_identity_limit=int(cfg["daily_identity"]),
            ),
            "counts": {
                "in_flight": len(self._in_flight),
                "queued": len(self._queued),
            },
        }

    def reset_for_tests(self) -> None:
        self._in_flight.clear()
        self._queued.clear()
        for fut in self._waiters.values():
            if not fut.done():
                fut.cancel()
        self._waiters.clear()
        self.quota = DailyTokenQuota()
        self._rust_config_pushed = False
        # 仅当明确允许时清 live host，避免 pytest 误伤正在跑的桌面会话
        import os

        if os.environ.get("TAKTON_LLM_TEST_CLEAR_HOST", "").lower() in (
            "1",
            "true",
            "yes",
        ):
            k = _rust_kernel()
            if k is not None:
                try:
                    k._call("llm_force_clear")
                except Exception as e:
                    logger.debug("rust llm_force_clear on reset: %s", e)


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
