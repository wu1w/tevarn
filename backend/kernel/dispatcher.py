"""PLAN 阶段 0.6：Workforce Dispatcher（唤醒执行器）。

「休眠-唤醒-续作」的实现：
- 休眠：无常驻 agent 进程——无事时什么都不跑（天然零成本休眠）
- 唤醒：inbox 有工单 → 为对应身份创建 kernel 进程（编制内权限/预算）
- 续作：身份有专属 workforce session（历史对话即 Episodic 上下文）+
  Identity Memory 注入 prompt（人格/职责/方法论常驻）

红线：
- 唤醒路径全程过 kernel.mediate（capabilities=身份权限档案）+
  token 预算扣减——异步入口不得绕过权限与预算（PLAN §3.f）
- 编制内串行：同一身份同时在手一单（InboxService.claim_next 保证）
- 单工单超时熔断（agent_inbox_item_timeout 秒）
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from backend.core.config import settings

logger = logging.getLogger(__name__)

_DEFAULT_ITEM_TIMEOUT = 600.0


class WorkforceDispatcher:
    """工单派遣器：扫描 inbox → 唤醒身份 → 执行 → 回写。"""

    def __init__(
        self,
        kernel: Any,
        inbox: Any,
        registry: Any,
        session_factory: Any,
        *,
        poll_seconds: float = 10.0,
        item_timeout: float = _DEFAULT_ITEM_TIMEOUT,
        executor: Any = None,
    ) -> None:
        self._kernel = kernel
        self._inbox = inbox
        self._registry = registry
        self._session_factory = session_factory
        self._poll_seconds = max(1.0, float(poll_seconds))
        self._item_timeout = max(30.0, float(item_timeout))
        # 执行器注入点：None = 生产路径（NexusAgentLoop 真 LLM）；
        # 测试/定制场景注入 async fn(ident, item, kernel_process_id, kernel) -> str
        self._executor = executor
        self._busy: set[str] = set()  # 在手单的身份 id（编制内串行）
        self._running = False
        # Worker 池（Alpha Review #2）：identity_id → 长生命周期 loop 实例
        self._workers: dict[str, Any] = {}
        # E4 统一停止：追踪在跑工单的 task / loop / process
        self._item_tasks: dict[str, asyncio.Task] = {}
        self._item_loops: dict[str, Any] = {}
        self._item_proc_ids: dict[str, str] = {}
        self._proc_to_item: dict[str, str] = {}
        # CEO 汇总：防同一批完成重复唤醒
        self._ceo_rollup_done: set[str] = set()
        self._ceo_rollup_inflight: set[str] = set()
        # Event wake: inbox.enqueue → nudge() so interactive assign is ~ms not poll floor
        self._wake: asyncio.Event = asyncio.Event()

    def nudge(self) -> None:
        """Wake run_forever immediately (safe from any thread/async context)."""
        try:
            self._wake.set()
        except Exception:
            pass

    async def run_forever(self) -> None:
        """后台主循环（lifespan _spawn_bg 拉起）。"""
        self._running = True
        logger.info(
            "workforce dispatcher started (poll=%.0fs, event-wake=on)",
            self._poll_seconds,
        )
        # 冷启动：后端重启后 SQL claimed 无 worker → 立即回收，避免等满 600s
        try:
            n = await self._inbox.reclaim_stale_claims(
                timeout_seconds=30.0,
                busy_identity_ids=set(),
                live_item_ids=set(),
                orphan_grace_seconds=0.0,
                force_all_orphans=True,
            )
            if n:
                logger.warning(
                    "dispatcher startup reclaimed %s orphan claimed job(s)", n
                )
        except Exception as e:
            logger.warning("dispatcher startup reclaim skip: %s", e)
        while self._running:
            # Clear before tick so wakes during tick still re-arm after sleep wait.
            self._wake.clear()
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("dispatcher tick 失败（下一轮继续）: %s", e)
            try:
                # Sleep until poll interval OR inbox.nudge() (assign latency ≈ ms)
                await asyncio.wait_for(self._wake.wait(), timeout=self._poll_seconds)
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                raise

    async def stop(self) -> None:
        self._running = False
        self.nudge()  # unblock wait_for so stop is prompt

    async def _worker_for(self, ident: Any) -> Any:
        """WorkforceWorker 池（Alpha Review #2）：per-identity 长生命周期
        loop 实例——repo 引用 / RAG 懒加载缓存 / context_manager 跨工单复用，
        不再每单重新装配。

        安全性：dispatcher 以 busy_identity_ids 保证同身份同时只派一单
        （tick 内串行），故 per-identity 单实例无并发冲突；
        run 级状态由 _reset_run_state() 在每次派发前显式归零。
        身份归档/能力大改后调用 evict_worker 释放。"""
        key = str(ident.id)
        loop = self._workers.get(key)
        if loop is None:
            from backend.agent import NexusAgentLoop
            from backend.kernel.ports import get_ws_manager

            # 直接用 repo，避免 kernel/dispatcher 依赖 FastAPI dependencies
            from backend.repositories.context_repo import (
                AsyncContextFlowRepository,
                AsyncCtxItemRepository,
            )
            from backend.repositories.message_repo import AsyncMessageRepository
            from backend.repositories.notification_repo import (
                AsyncNotificationRepository,
            )
            from backend.repositories.session_repo import AsyncSessionRepository
            from backend.repositories.task_repo import AsyncTaskRepository

            loop = NexusAgentLoop(
                session_repo=AsyncSessionRepository(),
                message_repo=AsyncMessageRepository(),
                task_repo=AsyncTaskRepository(),
                ctx_item_repo=AsyncCtxItemRepository(),
                context_flow_repo=AsyncContextFlowRepository(),
                ws_manager=get_ws_manager(),
                user_id=ident.user_id,
                notification_repo=AsyncNotificationRepository(),
            )
            self._workers[key] = loop
            logger.info("workforce worker 上岗 ident=%s name=%s", key[:8], ident.name)
        return loop

    def evict_worker(self, identity_id: Any) -> None:
        """身份归档/能力大改后释放其 worker（下次派发重新装配）。"""
        self._workers.pop(str(identity_id), None)

    def _effective_budget(
        self,
        ident: Any,
        instruction: str | None = None,
        item: Any | None = None,
    ) -> int | None:
        """有效预算：CEO 工单 payload.token_budget 优先，否则档案+任务类抬升。

        0 = 显式不限。CEO 指定只影响本工单进程，不改档案（除非 set_budget）。
        """
        try:
            from backend.agent.workforce_budget import resolve_job_budget

            payload = getattr(item, "payload", None) if item is not None else None
            budget, source = resolve_job_budget(
                ident, instruction or "", payload=payload
            )
            if source.startswith("ceo") or source == "auto":
                logger.info(
                    "job budget source=%s identity=%s budget=%s",
                    source,
                    getattr(ident, "name", "") or str(getattr(ident, "id", ""))[:8],
                    budget,
                )
            return budget
        except Exception:
            if ident.default_token_budget is not None:
                return ident.default_token_budget
            try:
                fallback = int(
                    getattr(settings, "agent_workforce_fallback_budget", 100_000)
                )
            except Exception:
                fallback = 100_000
            return fallback if fallback > 0 else None

    async def _build_memory_block(
        self, ident: Any, instruction: str, memory_entries: list[Any]
    ) -> tuple[str, str]:
        """身份记忆注入：委托 CrewMemoryAssembler（编制唯一读路径）。"""
        from backend.kernel.crew_memory import get_crew_memory_assembler

        asm = get_crew_memory_assembler(self._registry)
        result = await asm.build_inject_block(
            getattr(ident, "id", ident),
            instruction or "",
            mode="workforce",
            memory_entries=memory_entries,
        )
        return result.header, result.body

    async def _retrieve_identity_memory(
        self, ident: Any, query: str, *, top_k: int = 8
    ) -> str | None:
        """检索式身份记忆召回（Alpha Review #4）：向量模式可用时按
        工单相关性 top-k；不可用/无结果返回 None（调用方回落全量截断）。"""
        try:
            from backend.services.rag.capability import use_vector_rag

            if not use_vector_rag():
                return None
            from backend.services.rag.factory import RAGServiceFactory

            rag = RAGServiceFactory.get_service()
            docs = await rag.search_identity_memory(
                query, str(ident.id), top_k=top_k
            )
            if not docs:
                return None
            return "\n".join(
                f"- [{(d.payload or {}).get('kind', 'memory')}] {d.text}" for d in docs
            )
        except Exception as e:
            logger.debug("身份记忆检索跳过: %s", e)
            return None

    def _max_global_concurrent(self) -> int:
        try:
            return max(0, int(getattr(settings, "agent_dispatcher_max_global_concurrent", 8) or 8))
        except Exception:
            return 8

    def _max_identity_concurrent(self) -> int:
        """单身份并发。默认 1（编制内串行）；>1 时仍按 busy 集合近似。"""
        try:
            return max(1, int(getattr(settings, "agent_dispatcher_max_identity_concurrent", 1) or 1))
        except Exception:
            return 1

    def _shared_store(self) -> Any | None:
        return getattr(self._kernel, "_shared", None)

    def _busy_ttl_seconds(self) -> int:
        """Redis busy TTL ≥ 工单超时 + 缓冲，避免长任务中途锁过期双派。"""
        try:
            base = float(
                getattr(settings, "agent_inbox_item_timeout", self._item_timeout)
                or self._item_timeout
            )
        except Exception:
            base = self._item_timeout
        return max(120, int(base) + 120)

    def _merge_busy_identity_ids(self) -> set[str]:
        """本地 _busy ∪ Redis 分布式 busy（多 worker）。"""
        busy: set[str] = set(self._busy)
        store = self._shared_store()
        if store is None:
            return busy
        try:
            busy |= set(store.list_busy_identity_ids() or set())
        except Exception as e:
            logger.debug("list_busy_identity_ids skip: %s", e)
        return busy

    def _try_acquire_redis_busy(self, identity_id: str, item_id: str) -> bool:
        store = self._shared_store()
        if store is None:
            return True
        try:
            return bool(
                store.try_acquire_identity_busy(
                    str(identity_id),
                    str(item_id),
                    ttl=self._busy_ttl_seconds(),
                )
            )
        except Exception as e:
            logger.warning("redis busy acquire fail-open: %s", e)
            return True

    def _release_redis_busy(self, identity_id: str, item_id: str) -> None:
        store = self._shared_store()
        if store is None:
            return
        try:
            store.release_identity_busy(str(identity_id), str(item_id))
        except Exception as e:
            logger.debug("redis busy release: %s", e)

    def _refresh_redis_busy(self, identity_id: str, item_id: str) -> None:
        store = self._shared_store()
        if store is None:
            return
        try:
            store.refresh_identity_busy(
                str(identity_id), str(item_id), ttl=self._busy_ttl_seconds()
            )
        except Exception as e:
            logger.debug("redis busy refresh: %s", e)

    def _rust_tick_hooks(self) -> None:
        """Dispatcher tick: isolation OS reap (best-effort).

        Note: inbox_reclaim is done once in reclaim_stale_claims — avoid
        double-reclaim thrash each tick.
        """
        k = self._kernel
        if not hasattr(k, "_call"):
            return
        try:
            timeout = float(
                getattr(settings, "agent_inbox_item_timeout", self._item_timeout)
                or self._item_timeout
            )
            k._call("isolation_reap", {"max_age_secs": max(timeout, 60.0)})
        except Exception as e:
            logger.debug("rust isolation_reap: %s", e)

    async def _resync_pending_to_rust(self) -> None:
        """After host restart, SQL pending may have no Rust mirror — re-submit.

        Re-runs when kernel host_epoch advances (hard restart wiped in-memory inbox).
        """
        if not hasattr(self._kernel, "_call"):
            return
        try:
            from backend.kernel_rust.client import is_rust_host_available

            if not is_rust_host_available():
                return
        except Exception:
            return
        epoch = int(getattr(self._kernel, "_host_epoch", 0) or 0)
        last_epoch = getattr(self, "_pending_resync_epoch", None)
        if last_epoch is not None and int(last_epoch) == epoch and getattr(
            self, "_pending_resync_done", False
        ):
            return
        try:
            rows = await self._inbox.list_items(status="pending", limit=200)
        except Exception as e:
            logger.debug("list pending for resync: %s", e)
            return
        n = 0
        for it in rows or []:
            try:
                ident = await self._registry.get(it.identity_id)
                ikey = str(getattr(ident, "name", "") or it.identity_id)
                self._inbox.ensure_rust_pending(
                    identity_key=ikey,
                    instruction=str(it.instruction or ""),
                    priority=int(getattr(it, "priority", 0) or 0),
                    db_item_id=str(it.id),
                )
                n += 1
            except Exception:
                continue
        self._pending_resync_done = True
        self._pending_resync_epoch = epoch
        if n:
            logger.info(
                "resync pending→rust submitted=%s host_epoch=%s", n, epoch
            )

    @staticmethod
    def _identity_slot_key(ident: Any) -> str:
        """并发槽位键：与 identity_hire 的 id 一致（uuid，不用显示名）。

        进程 identity 仍是 ``wf:{uuid}``；host 编制缓存按 hire id 索引。
        """
        iid = str(getattr(ident, "id", "") or "").strip()
        if iid:
            return iid
        return str(getattr(ident, "name", "") or "").strip()

    def _identity_admit(self, ident: Any) -> bool:
        """Register + admit identity concurrency slot in Rust authority."""
        k = self._kernel
        if not hasattr(k, "_call"):
            return True
        try:
            key = self._identity_slot_key(ident)
            if not key:
                return True
            name = str(getattr(ident, "name", "") or key)
            role = str(getattr(ident, "role", "") or "")
            caps = list(getattr(ident, "capabilities", None) or [])
            # ensure identity exists in rust cache（id=uuid，name 仅展示）
            k._call(
                "identity_hire",
                {
                    "id": key,
                    "name": name,
                    "role": role,
                    "capabilities": caps,
                    "max_concurrent": 1,
                },
            )
            r = k._call("identity_admit", {"id": key})
            if isinstance(r, dict) and r.get("ok") is False:
                return False
            return True
        except Exception as e:
            # Permission error = at capacity
            msg = str(e).lower()
            if "max" in msg or "admit" in msg or "permission" in msg or "concurrent" in msg:
                logger.warning("identity_admit denied for %s: %s", getattr(ident, "name", ""), e)
                return False
            logger.debug("identity_admit soft-fail: %s", e)
            return True

    def _identity_release(self, ident: Any) -> None:
        """释放并发槽：只按 hire id（uuid）一次，避免 name 空操作噪音。"""
        k = self._kernel
        if not hasattr(k, "_call") or ident is None:
            return
        try:
            key = self._identity_slot_key(ident)
            if key:
                k._call("identity_release", {"id": key})
        except Exception as e:
            logger.debug("identity_release: %s", e)

    async def tick(self, *, wait: bool = False) -> int:
        """扫描一轮，派发所有可派工单。返回派发数。
        wait=True 时等待本轮派发的工单全部完成（测试/同步场景）。

        Claim 路径：Rust inbox_claim 权威（见 InboxService.claim_next）。
        """
        # Rust reclaim + isolation OS reap first
        try:
            self._rust_tick_hooks()
        except Exception as e:
            logger.debug("rust tick hooks: %s", e)
        # Align Rust claim lease with item timeout (once per process)
        try:
            if not getattr(self, "_rust_claim_timeout_synced", False):
                k = getattr(self, "_kernel", None)
                if k is not None and hasattr(k, "_call"):
                    timeout = float(
                        getattr(settings, "agent_inbox_item_timeout", self._item_timeout)
                        or self._item_timeout
                    )
                    # lease = item timeout + 5min grace so heartbeat keeps sticky
                    k._call(
                        "inbox_set_claim_timeout",
                        {"secs": max(120.0, timeout + 300.0)},
                    )
                    self._rust_claim_timeout_synced = True
        except Exception as e:
            logger.debug("inbox_set_claim_timeout skip: %s", e)
        # Bootstrap: re-mirror SQL pending into Rust after host restart
        try:
            await self._resync_pending_to_rust()
        except Exception as e:
            logger.debug("resync pending→rust skip: %s", e)
        # 回收超时 / 孤儿 claimed（worker 崩溃或后端重启残留）
        try:
            timeout = float(
                getattr(settings, "agent_inbox_item_timeout", self._item_timeout)
                or self._item_timeout
            )
            await self._inbox.reclaim_stale_claims(
                timeout_seconds=timeout,
                busy_identity_ids=set(self._busy) | self._merge_busy_identity_ids(),
                live_item_ids=set(self._item_tasks.keys()),
                orphan_grace_seconds=45.0,
            )
        except Exception as e:
            logger.debug("reclaim_stale_claims skip: %s", e)
        dispatched = 0
        tasks: list[asyncio.Task] = []
        max_global = self._max_global_concurrent()
        # Soft shrink under host memory pressure (prevents silent OOM of backend
        # when CEO + multi workforce + cargo peak together on Windows).
        try:
            from backend.core.process_guard import memory_pressure

            pressure = memory_pressure()
            if pressure == "critical":
                max_global = 1
                logger.warning(
                    "dispatcher memory_pressure=critical → cap concurrent=1"
                )
            elif pressure == "elevated" and max_global > 2:
                max_global = 2
                logger.info(
                    "dispatcher memory_pressure=elevated → cap concurrent=2"
                )
        except Exception:
            pass
        # F2：身份并发默认 1 → busy 集合即全局并发计数
        while True:
            if max_global > 0 and len(self._busy) >= max_global:
                logger.debug(
                    "dispatcher global concurrency cap hit (%s)", max_global
                )
                break
            item = await self._inbox.claim_next(
                busy_identity_ids=self._merge_busy_identity_ids()
            )
            if item is None:
                break
            iid = str(item.identity_id)
            item_key = str(item.id)
            # 多 worker：Redis SETNX 占坑；失败则退回 pending（防双派同身份）
            if not self._try_acquire_redis_busy(iid, item_key):
                logger.warning(
                    "identity %s busy on another worker; requeue item %s",
                    iid[:8],
                    item_key[:8],
                )
                try:
                    await self._inbox.release_claim_to_pending(
                        item.id, reason="redis_identity_busy"
                    )
                except Exception as e:
                    logger.warning("requeue after redis busy fail: %s", e)
                continue
            self._busy.add(iid)
            task = asyncio.create_task(self._run_item_guarded(item))
            self._item_tasks[item_key] = task
            tasks.append(task)
            dispatched += 1
        if wait and tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        return dispatched

    async def cancel_job(
        self,
        *,
        item_id: str | None = None,
        process_id: str | None = None,
        reason: str = "stopped by user",
    ) -> dict[str, Any]:
        """E4 统一停止：取消 agent loop + kernel process + 工单 cancelled。

        至少提供 item_id 或 process_id 之一。
        """
        out: dict[str, Any] = {
            "ok": False,
            "inbox_item_id": None,
            "process_id": None,
            "loop_stopped": False,
            "task_cancelled": False,
            "process_killed": False,
            "inbox_cancelled": False,
            "reason": reason,
        }
        iid = (item_id or "").strip() or None
        pid = (process_id or "").strip() or None
        if not iid and pid:
            iid = self._proc_to_item.get(pid)
        if not iid and not pid:
            return {**out, "error": "need item_id or process_id"}

        if iid:
            out["inbox_item_id"] = iid
            loop = self._item_loops.get(iid)
            if loop is not None and hasattr(loop, "stop"):
                try:
                    loop.stop()
                    out["loop_stopped"] = True
                except Exception as e:
                    logger.debug("cancel loop.stop: %s", e)
            task = self._item_tasks.get(iid)
            if task is not None and not task.done():
                task.cancel()
                out["task_cancelled"] = True
            if not pid:
                pid = self._item_proc_ids.get(iid)

        if pid:
            out["process_id"] = pid
            try:
                ended = await self._kernel.end_process(
                    pid, state="killed", reason=reason
                )
                out["process_killed"] = ended is not None
            except Exception as e:
                logger.debug("cancel end_process: %s", e)

        if iid:
            try:
                cancelled = await self._inbox.cancel(
                    iid, reason=reason, process_id=pid
                )
                out["inbox_cancelled"] = cancelled is not None
            except Exception as e:
                logger.debug("cancel inbox: %s", e)
                out["inbox_cancelled"] = False

        out["ok"] = bool(
            out["loop_stopped"]
            or out["task_cancelled"]
            or out["process_killed"]
            or out["inbox_cancelled"]
        )
        return out

    async def _resolve_notify_user_id(
        self,
        identity: Any | None,
        *,
        process_id: str | None = None,
    ) -> Any | None:
        """通知落点：identity.user_id → process meta owner → 单用户默认 admin。

        多用户禁止魔术邮箱静默吞掉；与 create_process meta.owner_user_id 对齐。
        """
        uid = getattr(identity, "user_id", None) if identity is not None else None
        if uid is not None:
            return uid

        # 进程 meta（编制 create_process 已写 owner_user_id）
        pid = str(process_id or "").strip()
        if pid:
            try:
                k = self._kernel
                proc = k.get_process(pid) if hasattr(k, "get_process") else None
                meta = {}
                if proc is not None:
                    meta = dict(getattr(proc, "meta", None) or {})
                    if not meta and hasattr(proc, "to_dict"):
                        meta = dict((proc.to_dict() or {}).get("meta") or {})
                for key in ("owner_user_id", "user_id", "ceo_user_id", "owner_id"):
                    mid = meta.get(key)
                    if mid:
                        return mid
            except Exception as e:
                logger.debug("notify resolve process meta: %s", e)

        # 仅单用户：回落默认 admin（兼容未 seed identity.user_id 的本机）
        try:
            from backend.core.config import settings

            if not bool(getattr(settings, "single_user_mode", True)):
                logger.warning(
                    "workforce notify: no owner (identity=%s process=%s) multi-user skip",
                    str(getattr(identity, "id", "") or "")[:8],
                    pid[:8] if pid else "-",
                )
                return None
        except Exception:
            pass
        try:
            from backend.repositories.user_repo import AsyncUserRepository

            u = await AsyncUserRepository().get_by_email("admin@tevarn.dev")
            return u.id if u is not None else None
        except Exception as e:
            logger.debug("notify default admin skip: %s", e)
            return None

    async def _notify_owner(
        self,
        *,
        kind: str,
        title: str,
        content: str,
        identity: Any | None = None,
        item_id: Any | None = None,
        process_id: str | None = None,
    ) -> None:
        """工单完成/失败：写 notification 表 + 向主人所有 live WS 推 type:notification。"""
        try:
            from backend.repositories.notification_repo import (
                AsyncNotificationRepository,
            )

            uid = await self._resolve_notify_user_id(
                identity, process_id=process_id
            )
            if uid is None:
                return
            repo = AsyncNotificationRepository()
            row = await repo.create(
                {
                    "user_id": uid,
                    "type": kind,
                    "title": title[:256],
                    "content": content[:4000],
                    "is_read": False,
                    "data": {
                        "identity_id": str(getattr(identity, "id", "") or ""),
                        "identity_name": str(getattr(identity, "name", "") or ""),
                        "inbox_item_id": str(item_id or ""),
                        "process_id": str(process_id or ""),
                        "source": "workforce_dispatcher",
                    },
                    "source_id": str(item_id or "")[:64] or None,
                }
            )
            # 实时推送：聊天页 useWebSocket 会写入 notificationStore；无 chat WS 时
            # DomainEventBridge 仍靠 job.* toast 兜底
            try:
                import uuid as _uuid

                from backend.api.websocket import manager as ws_manager

                uid_u = uid if isinstance(uid, _uuid.UUID) else _uuid.UUID(str(uid))
                payload = {
                    "type": "notification",
                    "id": str(getattr(row, "id", "") or ""),
                    "user_id": str(uid),
                    "notification_type": kind,
                    "title": title[:256],
                    "message": content[:500],
                    "content": content[:4000],
                    "is_read": False,
                    "created_at": (
                        getattr(row, "created_at", None).isoformat()
                        if getattr(row, "created_at", None)
                        else None
                    ),
                    "data": {
                        "identity_id": str(getattr(identity, "id", "") or ""),
                        "identity_name": str(getattr(identity, "name", "") or ""),
                        "inbox_item_id": str(item_id or ""),
                        "process_id": str(process_id or ""),
                        "source": "workforce_dispatcher",
                        "kind": kind,
                    },
                    "link": "/agents",
                }
                await ws_manager.broadcast_to_user(uid_u, payload)
            except Exception as pe:
                logger.debug("workforce notify WS push skip: %s", pe)
        except Exception as e:
            logger.debug("workforce notify skip: %s", e)

    async def _load_item_result_snip(self, item_id: Any, *, max_len: int = 1200) -> str:
        try:
            rows = await self._inbox.list_items(status="done", limit=80)
            for it in rows:
                if str(it.id) == str(item_id):
                    body = (it.result or "").strip()
                    if not body:
                        return ""
                    one = body.replace("\n", " ")
                    return one[:max_len]
        except Exception as e:
            logger.debug("load item result snip: %s", e)
        return ""

    async def _maybe_wake_ceo_rollup(self, *, item_id: Any, identity: Any | None) -> None:
        """当一批关联工单全部终态时，唤醒 CEO 会话汇总汇报主人。

        关联批次优先级：
        1) 包含该工单的 open 项目组
        2) 同 payload.steward_session_id 的 crew_steward 工单
        """
        try:
            batch_key, item_ids, steward_sid, title = await self._resolve_rollup_batch(
                str(item_id)
            )
            if not batch_key or not item_ids:
                return
            if batch_key in self._ceo_rollup_done or batch_key in self._ceo_rollup_inflight:
                return

            # 是否全部终态
            states = await self._batch_item_states(item_ids)
            if not states:
                return
            terminal = {"done", "failed", "dead", "dropped", "cancelled"}
            if any(st not in terminal for st in states.values()):
                return
            if not any(st == "done" for st in states.values()):
                # 全失败也值得汇报
                pass

            self._ceo_rollup_inflight.add(batch_key)
            try:
                await self._run_ceo_rollup(
                    batch_key=batch_key,
                    item_ids=item_ids,
                    steward_session_id=steward_sid,
                    title=title,
                    identity=identity,
                )
                self._ceo_rollup_done.add(batch_key)
                await self._mark_project_rollup_done(batch_key)
            finally:
                self._ceo_rollup_inflight.discard(batch_key)
        except Exception as e:
            logger.warning("ceo rollup failed: %s", e)

    async def _resolve_rollup_batch(
        self, item_id: str
    ) -> tuple[str | None, list[str], str | None, str]:
        """返回 (batch_key, item_ids, steward_session_id, title)。"""
        # 1) 项目组
        try:
            from sqlalchemy import select

            from backend.database import AsyncSessionLocal
            from backend.models.project_group import ProjectGroup

            async with AsyncSessionLocal() as session:
                rows = list(
                    (
                        await session.execute(
                            select(ProjectGroup)
                            .where(ProjectGroup.status == "open")
                            .order_by(ProjectGroup.updated_at.desc())
                            .limit(40)
                        )
                    )
                    .scalars()
                    .all()
                )
            for g in rows:
                meta = dict(g.meta or {})
                if meta.get("ceo_rollup_at"):
                    continue
                tasks = list(g.tasks or [])
                ids = [str(t.get("inbox_item_id")) for t in tasks if t.get("inbox_item_id")]
                if item_id in ids and ids:
                    sid = str(meta.get("steward_session_id") or "") or None
                    return f"pg:{g.id}", ids, sid, str(g.title or "项目组")
        except Exception as e:
            logger.debug("resolve project batch: %s", e)

        # 2) steward_session_id 批次
        try:
            done_like = await self._inbox.list_items(status="done", limit=80)
            pending = await self._inbox.list_items(status="pending", limit=80)
            claimed = await self._inbox.list_items(status="claimed", limit=80)
            failed = await self._inbox.list_items(status="failed", limit=40)
            all_items = list(done_like) + list(pending) + list(claimed) + list(failed)
            cur = next((it for it in all_items if str(it.id) == item_id), None)
            if cur is None:
                # 可能刚 complete，再扫 done
                for it in done_like:
                    if str(it.id) == item_id:
                        cur = it
                        break
            if cur is None:
                return None, [], None, ""
            payload = dict(getattr(cur, "payload", None) or {})
            sid = str(payload.get("steward_session_id") or "").strip() or None
            if not sid:
                return None, [], None, ""
            ptitle = str(payload.get("project_title") or "").strip()
            batch_ids: list[str] = []
            for it in all_items:
                pl = dict(getattr(it, "payload", None) or {})
                if str(pl.get("steward_session_id") or "") == sid:
                    if ptitle and str(pl.get("project_title") or "") not in ("", ptitle):
                        continue
                    batch_ids.append(str(it.id))
            batch_ids = list(dict.fromkeys(batch_ids))
            if item_id not in batch_ids:
                batch_ids.append(item_id)
            key = f"sess:{sid}:{ptitle or 'default'}"
            return key, batch_ids, sid, ptitle or "编制任务"
        except Exception as e:
            logger.debug("resolve session batch: %s", e)
            return None, [], None, ""

    async def _batch_item_states(self, item_ids: list[str]) -> dict[str, str]:
        want = set(item_ids)
        out: dict[str, str] = {}
        for st in ("pending", "claimed", "done", "failed", "dead", "dropped", "cancelled"):
            try:
                rows = await self._inbox.list_items(status=st, limit=200)
            except Exception:
                continue
            for it in rows:
                iid = str(it.id)
                if iid in want:
                    out[iid] = st
        # 未扫到的当 pending（保守：不触发）
        for iid in want:
            out.setdefault(iid, "pending")
        return out

    async def _mark_project_rollup_done(self, batch_key: str) -> None:
        if not batch_key.startswith("pg:"):
            return
        pg_id = batch_key[3:]
        try:
            import time as _time

            from sqlalchemy import select

            from backend.database import AsyncSessionLocal
            from backend.models.project_group import ProjectGroup

            async with AsyncSessionLocal() as session:
                row = (
                    await session.execute(
                        select(ProjectGroup).where(ProjectGroup.id == uuid.UUID(pg_id))
                    )
                ).scalar_one_or_none()
                if row is None:
                    return
                meta = dict(row.meta or {})
                meta["ceo_rollup_at"] = _time.time()
                row.meta = meta
                # 全完成后可标 done（侧栏）
                try:
                    row.status = "done"
                except Exception:
                    pass
                await session.commit()
        except Exception as e:
            logger.debug("mark project rollup: %s", e)

    async def _find_steward_session_id(
        self, preferred: str | None, *, user_id: uuid.UUID | None
    ) -> uuid.UUID | None:
        if preferred:
            try:
                sid = uuid.UUID(str(preferred))
                from backend.repositories.session_repo import AsyncSessionRepository

                if await AsyncSessionRepository().get_by_id(sid) is not None:
                    return sid
            except Exception:
                pass
        if user_id is None:
            return None
        try:
            from sqlalchemy import select

            from backend.agent.workforce_dispatch import is_steward_contact
            from backend.database import AsyncSessionLocal
            from backend.models.session import Session

            async with AsyncSessionLocal() as session:
                rows = list(
                    (
                        await session.execute(
                            select(Session)
                            .where(Session.user_id == user_id)
                            .order_by(Session.updated_at.desc())
                            .limit(40)
                        )
                    )
                    .scalars()
                    .all()
                )
            for row in rows:
                cfg = dict(row.config or {})
                if cfg.get("source") == "workforce":
                    continue
                contact = str(cfg.get("contact_agent") or "")
                ident = str(cfg.get("identity") or "")
                if is_steward_contact(contact) or is_steward_contact(ident):
                    return row.id
                # 启发式：identity 文案含 CEO/大管家
                if "CEO" in ident or "大管家" in ident or "steward" in ident.lower():
                    return row.id
        except Exception as e:
            logger.debug("find steward session: %s", e)
        return None

    async def _run_ceo_rollup(
        self,
        *,
        batch_key: str,
        item_ids: list[str],
        steward_session_id: str | None,
        title: str,
        identity: Any | None,
    ) -> None:
        # 组装结果正文
        name = getattr(identity, "name", "") or ""
        blocks: list[str] = []
        try:
            idents = await self._registry.list(status=None)
            names = {str(i.id): str(i.name or "") for i in idents}
        except Exception:
            names = {}

        n_done = n_failed = n_dead = 0
        # audit-fix(#4)：失败回调 prompt 需附带剩余可 requeue 次数
        _requeue_left: int | None = None
        for st in ("done", "failed", "dead", "cancelled"):
            try:
                rows = await self._inbox.list_items(status=st, limit=100)
            except Exception:
                continue
            for it in rows:
                if str(it.id) not in item_ids:
                    continue
                if st == "done":
                    n_done += 1
                elif st == "failed":
                    n_failed += 1
                elif st == "dead":
                    n_dead += 1
                if st in ("failed", "dead"):
                    try:
                        from backend.kernel.inbox import requeue_remaining_of

                        _left = requeue_remaining_of(it)
                        _requeue_left = (
                            _left
                            if _requeue_left is None
                            else min(_requeue_left, _left)
                        )
                    except Exception:
                        pass
                nm = names.get(str(it.identity_id), str(it.identity_id)[:8])
                body = (it.result or it.error or "").strip() or "（无正文）"
                # 短摘要：避免把数千字工单全文塞进 CEO 会话历史
                try:
                    from backend.core.config import settings as _cfg

                    max_b = int(
                        getattr(_cfg, "agent_rollup_max_block_chars", 500) or 500
                    )
                except Exception:
                    max_b = 500
                if len(body) > max_b:
                    body = body[:max_b] + "\n…[摘要截断，详情见 crew_steward results]"
                blocks.append(
                    f"### [{st}] {nm}\n"
                    f"任务：{(it.instruction or '')[:160]}\n"
                    f"结果：{body}\n"
                )

        if not blocks:
            logger.info("ceo rollup skip empty batch=%s", batch_key)
            return

        all_ok = n_failed == 0 and n_dead == 0
        batch_verdict = (
            "全部成功"
            if all_ok
            else f"有失败（done={n_done} failed={n_failed} dead={n_dead}）"
        )

        owner = None
        try:
            # 与 notify 同口径：identity → process meta → 单用户 admin
            owner = await self._resolve_notify_user_id(identity)
        except Exception as e:
            logger.debug("ceo rollup owner resolve: %s", e)
            owner = None
        if owner is None:
            logger.warning("ceo rollup: no owner user")
            return

        sid = await self._find_steward_session_id(steward_session_id, user_id=owner)
        notify_kind = "task_complete" if all_ok else "task_failed"
        notify_title = (
            f"项目完成 · {title}" if all_ok else f"项目部分失败 · {title} · {batch_verdict}"
        )
        if sid is None:
            logger.warning("ceo rollup: no steward session for batch=%s", batch_key)
            # 仍通知主人：附完整摘要
            await self._notify_owner(
                kind=notify_kind,
                title=notify_title,
                content="\n\n".join(blocks)[:3900],
                identity=identity,
                item_id=item_ids[0] if item_ids else None,
            )
            return

        honesty = (
            "批次状态：**全部成功**。可写完成结论。\n"
            if all_ok
            else (
                f"批次状态：**{batch_verdict}**。\n"
                "硬性纪律（违反即幻觉）：\n"
                "- **禁止**写「完整/已全部完成/全绿/体检通过」等完成口径；\n"
                "- 总结论第一句必须点明失败人数与主因（如 Budget Exceeded / 超时）；\n"
                "- 分员工要点时 failed/dead 必须单独列出原因，不得合并进「成功」；\n"
                "- 不得把「报告框架/预期结果」当成已执行检查。\n"
            )
        )
        # 失败批次：附上待批提权，强制 CEO 先 grant 再汇报
        grant_block = ""
        try:
            from backend.agent.steward_auto_grant import format_pending_grants_brief

            pb = format_pending_grants_brief(limit=20)
            if pb and not all_ok:
                grant_block = (
                    "\n\n【提权优先】若失败含 outside_identity_caps / need_cap：\n"
                    "先 `crew_steward pending_grants` → "
                    "`grant_caps name=… capabilities=[…] requeue=true`，"
                    "**禁止**只写「请主人批准」。\n"
                    + pb
                    + "\n"
                )
                # audit-fix(#4)：requeue 建议附剩余次数；用尽则改为建议人工介入
                if _requeue_left is not None:
                    if _requeue_left > 0:
                        grant_block += (
                            f"（本批失败单剩余可 requeue 次数：{_requeue_left}；"
                            "用尽后禁止再 requeue，须人工介入）\n"
                        )
                    else:
                        grant_block = (
                            "\n\n【人工介入】本批失败单的 requeue 次数已用尽"
                            "（死单防无限复活上限）：**禁止**再 grant_caps requeue "
                            "或重派同一批工单；请如实汇报失败主因，"
                            "并建议主人人工排查（指令可行性 / 能力 / 预算）后再手动处理。\n"
                            + pb
                            + "\n"
                        )
        except Exception:
            grant_block = ""
        body_joined = "\n".join(blocks)
        try:
            from backend.core.config import settings as _cfg

            max_p = int(getattr(_cfg, "agent_rollup_max_prompt_chars", 2400) or 2400)
            max_iter = int(getattr(_cfg, "agent_rollup_max_iterations", 4) or 4)
        except Exception:
            max_p, max_iter = 2400, 4
        if len(body_joined) > max_p:
            body_joined = body_joined[:max_p] + "\n…[批次摘要已截断]"
        prompt = (
            f"【系统·编制自动回调】你派发的「{title}」相关工单已全部结束"
            f"（触发员工：{name or '—'}）。\n"
            f"{honesty}"
            f"{grant_block}"
            "请**立即**用简短中文汇报（勿再开长工具链）：\n"
            "1. 总结论（与批次状态一致）\n"
            "2. 分员工一句话要点（[done]/[failed]/[dead]）\n"
            "3. 失败时的下一步（抬预算 / grant_caps / 拆单）\n"
            "禁止再次 hire/assign 同一批；勿把下方原文整段复读给主人。\n\n"
            + body_joined
        )

        try:
            from backend.agent import NexusAgentLoop
            from backend.kernel.ports import get_ws_manager
            from backend.repositories.context_repo import (
                AsyncContextFlowRepository,
                AsyncCtxItemRepository,
            )
            from backend.repositories.message_repo import AsyncMessageRepository
            from backend.repositories.notification_repo import (
                AsyncNotificationRepository,
            )
            from backend.repositories.session_repo import AsyncSessionRepository
            from backend.repositories.task_repo import AsyncTaskRepository

            loop = NexusAgentLoop(
                session_repo=AsyncSessionRepository(),
                message_repo=AsyncMessageRepository(),
                task_repo=AsyncTaskRepository(),
                ctx_item_repo=AsyncCtxItemRepository(),
                context_flow_repo=AsyncContextFlowRepository(),
                ws_manager=get_ws_manager(),
                user_id=owner,
                notification_repo=AsyncNotificationRepository(),
            )
            # 汇总轮极短：避免再烧 10 轮工具。
            # 必须关掉 auto_continue，否则 4 轮/段 × 5 段 ≈ 20 轮，
            # 且回调正文里的「写报告」会被误判成 coding write-intent。
            try:
                loop.max_iterations = min(
                    int(getattr(loop, "max_iterations", 12) or 12), max_iter
                )
            except Exception:
                pass
            loop._auto_continue = False
            loop._rollup_turn = True
            logger.info(
                "ceo rollup start batch=%s session=%s items=%s",
                batch_key,
                str(sid)[:8],
                len(item_ids),
            )
            text = await loop.run(sid, prompt, attachments=None, mode="default")
            logger.info(
                "ceo rollup done batch=%s out_len=%s",
                batch_key,
                len(text or ""),
            )
            # 铃铛再推一条「汇总已写入 CEO 会话」
            await self._notify_owner(
                kind=notify_kind,
                title=(
                    f"CEO 已汇总 · {title}"
                    if all_ok
                    else f"CEO 已汇总（含失败）· {title}"
                ),
                content=(text or "（汇总已写入管家会话，请打开与 CEO 的对话查看）")[:1800],
                identity=identity,
                item_id=item_ids[0] if item_ids else None,
            )
        except Exception as e:
            logger.exception("ceo rollup run failed: %s", e)
            await self._notify_owner(
                kind="task_failed",
                title=f"CEO 汇总失败 · {title}",
                content=f"{e!s}"[:400],
                identity=identity,
                item_id=item_ids[0] if item_ids else None,
            )

    async def _run_item_guarded(self, item: Any) -> None:
        proc_id_holder: dict[str, str | None] = {"id": None}
        item_key = str(item.id)
        ident = None
        try:
            try:
                ident = await self._registry.get(item.identity_id)
            except Exception:
                ident = None
            # 长任务续期 Redis busy，避免 TTL 中途过期被他 worker 双派
            async def _run_with_busy_heartbeat() -> None:
                hb_stop = asyncio.Event()

                touch_fail_streak = {"n": 0}

                async def _hb() -> None:
                    interval = max(30.0, min(120.0, self._busy_ttl_seconds() / 4))
                    while not hb_stop.is_set():
                        try:
                            await asyncio.wait_for(hb_stop.wait(), timeout=interval)
                            break
                        except asyncio.TimeoutError:
                            self._refresh_redis_busy(str(item.identity_id), item_key)
                            # Claim lease heartbeat (SQL + Rust) — sticky while worker runs
                            try:
                                ok = await self._inbox.touch_claim(item.id)
                                if ok is False:
                                    touch_fail_streak["n"] += 1
                                else:
                                    touch_fail_streak["n"] = 0
                            except Exception as _te:
                                touch_fail_streak["n"] += 1
                                logger.debug("touch_claim skip: %s", _te)
                            # 连续失败：claim 可能已被 reclaim → 主动停本单防双执行
                            if touch_fail_streak["n"] >= 3:
                                logger.warning(
                                    "touch_claim failed %s times — stop item %s",
                                    touch_fail_streak["n"],
                                    str(item.id)[:8],
                                )
                                loop = self._item_loops.get(item_key)
                                if loop is not None and hasattr(loop, "stop"):
                                    try:
                                        loop.stop()
                                    except Exception:
                                        pass
                                hb_stop.set()
                                break

                hb_task = asyncio.create_task(_hb())
                try:
                    return await self._run_item(item, proc_id_holder=proc_id_holder)
                finally:
                    hb_stop.set()
                    try:
                        await hb_task
                    except Exception:
                        pass

            finish_status = await asyncio.wait_for(
                _run_with_busy_heartbeat(), timeout=self._item_timeout
            )
            # finish_status: completed | budget_failed | failed | skipped
            # 预算/逻辑失败不得发 task_complete（否则主人收到「完成」但 DB 是 fail）
            result_snip = await self._load_item_result_snip(item.id)
            status = str(finish_status or "completed")
            _pid = proc_id_holder.get("id") or self._item_proc_ids.get(item_key)
            if status == "budget_failed":
                await self._notify_owner(
                    kind="task_failed",
                    title=f"工单预算中断 · {getattr(ident, 'name', '员工')}",
                    content=(
                        (result_snip or "")[:1800]
                        or "预算耗尽，未完成实质工作 · "
                        + (item.instruction or "")[:120]
                    ),
                    identity=ident,
                    item_id=item.id,
                    process_id=_pid,
                )
            elif status == "failed":
                await self._notify_owner(
                    kind="task_failed",
                    title=f"工单失败 · {getattr(ident, 'name', '员工')}",
                    content=(
                        (result_snip or "")[:1800]
                        or (item.instruction or "")[:200]
                    ),
                    identity=ident,
                    item_id=item.id,
                    process_id=_pid,
                )
            elif status == "skipped":
                logger.info(
                    "inbox item skipped (no notify) id=%s",
                    str(getattr(item, "id", ""))[:8],
                )
            else:
                await self._notify_owner(
                    kind="task_complete",
                    title=f"工单完成 · {getattr(ident, 'name', '员工')}",
                    content=(
                        (result_snip or "")[:1800]
                        or (item.instruction or "")[:200]
                    ),
                    identity=ident,
                    item_id=item.id,
                    process_id=_pid,
                )
                # 仅真正完成才触发 CEO rollup
                try:
                    asyncio.create_task(
                        self._maybe_wake_ceo_rollup(item_id=item.id, identity=ident),
                        name=f"ceo-rollup-{str(item.id)[:8]}",
                    )
                except Exception as e:
                    logger.debug("schedule ceo rollup skip: %s", e)
        except asyncio.TimeoutError:
            logger.warning("工单 %s 超时（%.0fs）", item.id, self._item_timeout)
            pid = proc_id_holder.get("id")
            if pid:
                try:
                    await self._kernel.end_process(pid, state="killed", reason=f"inbox timeout {self._item_timeout:.0f}s")
                except Exception as e:
                    logger.debug("timeout end_process: %s", e)
            await self._inbox.fail(item.id, f"执行超时（{self._item_timeout:.0f}s）", process_id=pid)
            await self._notify_owner(
                kind="task_failed",
                title=f"工单超时 · {getattr(ident, 'name', '员工')}",
                content=f"超时 {self._item_timeout:.0f}s · {(item.instruction or '')[:160]}",
                identity=ident,
                item_id=item.id,
                process_id=pid,
            )
        except asyncio.CancelledError:
            # E4：用户/API 取消 → 工单 cancelled + process killed，不进重试/死信
            pid = proc_id_holder.get("id") or self._item_proc_ids.get(item_key)
            if pid:
                try:
                    await self._kernel.end_process(
                        pid, state="killed", reason="cancelled by user"
                    )
                except Exception as e:
                    logger.debug("cancel end_process: %s", e)
            try:
                await self._inbox.cancel(
                    item.id, reason="cancelled by user", process_id=pid
                )
            except Exception as e:
                logger.debug("cancel inbox on CancelledError: %s", e)
            return
        except Exception as e:
            logger.error("工单 %s 执行异常: %s", item.id, e)
            await self._inbox.fail(item.id, str(e), process_id=proc_id_holder.get("id"))
            await self._notify_owner(
                kind="task_failed",
                title=f"工单失败 · {getattr(ident, 'name', '员工')}",
                content=f"{e!s}"[:200] + " · " + (item.instruction or "")[:120],
                identity=ident,
                item_id=item.id,
                process_id=proc_id_holder.get("id"),
            )
        finally:
            self._busy.discard(str(item.identity_id))
            self._release_redis_busy(str(item.identity_id), item_key)
            self._item_tasks.pop(item_key, None)
            self._item_loops.pop(item_key, None)
            old_pid = self._item_proc_ids.pop(item_key, None)
            if old_pid:
                self._proc_to_item.pop(old_pid, None)
            held = proc_id_holder.get("id")
            if held:
                self._proc_to_item.pop(str(held), None)
            # release Rust identity concurrency slot
            try:
                self._identity_release(ident)
            except Exception:
                pass

    async def _run_item(
        self, item: Any, *, proc_id_holder: dict | None = None
    ) -> str:
        """唤醒身份执行一单。返回终态：completed | budget_failed | failed | skipped。

        进程归属：executor 路径由 dispatcher 建进程（审计锚点）；
        生产 loop 路径由 loop._run_inner 建进程（带编制选项），
        dispatcher 回读进程 id 关联工单。
        """
        ident = await self._registry.get(item.identity_id)
        if ident is None or ident.status != "active":
            await self._inbox.fail(item.id, "身份不存在或已停用")
            return "failed"

        # Rust identity admit (concurrency authority) before waking worker
        if not self._identity_admit(ident):
            try:
                await self._inbox.release_claim_to_pending(
                    item.id, reason="identity_admit_denied"
                )
            except Exception:
                await self._inbox.fail(item.id, "identity admit denied (at capacity)")
            # admit 失败未占槽：外层 finally 的 identity_release 必须幂等
            return "skipped"

        item_key = str(item.id)
        # P1-7: 工单级 rewind 起点
        try:
            from backend.agent.job_rewind import begin_job_snapshot

            begin_job_snapshot(item_key, label=(item.instruction or "")[:80])
        except Exception:
            pass
        if self._executor is not None:
            # 身份键必须用 uuid（wf:{id}），禁止用显示名——预算/org 聚合依赖此约定
            _budget = self._effective_budget(
                ident, getattr(item, "instruction", None), item=item
            )
            _owner = getattr(ident, "user_id", None)
            _owner_s = str(_owner) if _owner else None
            kernel_proc = await self._kernel.create_process(
                f"wf:{ident.id}",
                session_id=None,
                capabilities=list(ident.capabilities) if ident.capabilities is not None else None,
                token_budget=_budget,
                meta={
                    "inbox_item_id": str(item.id),
                    "identity_id": str(ident.id),
                    "source": item.source,
                    "identity_name": getattr(ident, "name", None),
                    "token_budget_applied": _budget,
                    # 多用户直接归属：list/top-up 不必再绕 identity 反查
                    "user_id": _owner_s,
                    "owner_user_id": _owner_s,
                },
            )
            if proc_id_holder is not None:
                proc_id_holder["id"] = kernel_proc.id
            self._item_proc_ids[item_key] = kernel_proc.id
            self._proc_to_item[kernel_proc.id] = item_key
            await self._kernel.mark_running(kernel_proc.id)
            try:
                result = await self._executor(ident, item, kernel_proc.id, self._kernel)
            except Exception as e:
                await self._kernel.end_process(kernel_proc.id, state="failed", reason=str(e)[:200])
                raise
            return await self._finish_item(item, result, process_id=kernel_proc.id)

        result, proc_id = await self._execute_with_loop(ident, item, item_key=item_key)
        if proc_id_holder is not None:
            proc_id_holder["id"] = proc_id
        if proc_id:
            self._item_proc_ids[item_key] = str(proc_id)
            self._proc_to_item[str(proc_id)] = item_key
        return await self._finish_item(item, result, process_id=proc_id)

    async def _finish_item(
        self, item: Any, result: str, *, process_id: str | None
    ) -> str:
        """Budget 中断 → fail（可重试/进死信）；正常 → complete。

        返回终态字符串供 _run_item_guarded 发正确通知：
        completed | budget_failed | failed
        """
        text = result or ""
        try:
            from backend.agent.workforce_budget import is_budget_exceeded_result

            budget_fail = is_budget_exceeded_result(text)
        except Exception:
            budget_fail = "[Budget Exceeded]" in text

        if budget_fail:
            if process_id:
                try:
                    await self._kernel.end_process(
                        process_id, state="failed", reason="budget_exceeded"
                    )
                except Exception:
                    pass
            # 固定短摘要：丢掉模型的长「报告框架」，避免主人误读为完成
            try:
                from backend.agent.workforce_budget import budget_fail_system_summary

                summary = budget_fail_system_summary(
                    instruction=str(getattr(item, "instruction", "") or ""),
                    raw=text,
                    process_id=process_id,
                )
            except Exception:
                summary = (
                    "[Budget Exceeded] 本工单因预算中断，未完成实质检查。"
                    "请勿将长文报告框架当作结论。"
                )
            await self._inbox.fail(
                item.id,
                summary[:4000],
                process_id=process_id,
                result=summary,
                terminal=True,
            )
            logger.warning(
                "inbox item budget-fail id=%s process=%s",
                str(getattr(item, "id", ""))[:8],
                (process_id or "")[:8],
            )
            return "budget_failed"

        if process_id:
            try:
                await self._kernel.end_process(process_id, state="completed")
            except Exception:
                pass
        await self._inbox.complete(item.id, text, process_id=process_id)
        return "completed"

    async def _execute_with_loop(
        self, ident: Any, item: Any, *, item_key: str | None = None
    ) -> tuple[str, str | None]:
        """构造 prompt（身份记忆注入）+ 跑一轮 loop（复用身份的专属 session 续作）。
        返回 (结果文本, kernel 进程 id)——进程由 loop 创建（带编制选项）。"""
        # Identity Memory（Alpha Review #4）：条目少 → 全量硬注入（人格/职责
        # 需要常驻）；条目超阈值 → 按工单相关性检索 top-k 注入（防 prompt 膨胀），
        # 检索不可用回落全量截断
        memory_entries = await self._registry.current_memory(ident.id)
        memory_header, memory_text = await self._build_memory_block(
            ident, item.instruction, memory_entries
        )

        prompt = (
            f"【工作任务】你是 «{ident.name}»"
            + (f"——{ident.role}" if ident.role else "")
            + f"\n\n{memory_header}\n{memory_text}\n"
            + f"\n## 本次工单（来源：{item.source}）\n{item.instruction.strip()}\n"
        )
        if item.payload:
            import json

            prompt += f"\n附加上下文：{json.dumps(item.payload, ensure_ascii=False)[:2000]}\n"
        prompt += (
            "\n要求：你已挂载本环境工具（含 file_read/grep/glob/command 等，以实际列表为准），"
            "请直接调用工具完成任务，不要声称无法访问文件系统；"
            "最终回复给出精炼、可验证的结果。"
            "\n**预算纪律**：若无法在有限工具轮次内完成，只输出「已完成/未完成清单」，"
            "禁止写「报告框架/预期结果」冒充检查结论；glob 默认跳过 node_modules/.git/dist。"
        )
        try:
            from backend.agent.workforce_budget import split_hint_for_instruction

            _hint = split_hint_for_instruction(str(item.instruction or ""))
            if _hint:
                prompt += f"\n\n系统提示：{_hint}\n"
        except Exception:
            pass
        try:
            from backend.agent.dispatch_grounding import worker_hygiene_block

            prompt += worker_hygiene_block()
        except Exception:
            pass

        session_id = await self._workforce_session_id(ident)

        # 防双 Run/双进程：开工前清理同 identity 残留非终态 kernel 进程
        # （上一单未 end_process / verifying 悬挂 / 多 worker 竞态）
        try:
            killed = await self._kernel.retire_live_identity_processes(
                f"wf:{ident.id}",
                reason=f"new inbox job {str(item.id)[:8]} supersedes stale process",
            )
            if killed:
                logger.warning(
                    "workforce preflight retired %s stale process(es) for %s",
                    len(killed),
                    str(ident.id)[:8],
                )
        except Exception as e:
            logger.warning("workforce preflight retire skipped: %s", e)

        # Worker 池复用（Alpha Review #2）：同身份跨工单共享 loop 实例，
        # run 级状态显式归零后再上岗（防跨工单泄漏红线）
        loop = await self._worker_for(ident)
        if item_key:
            self._item_loops[item_key] = loop
        loop._reset_run_state()
        loop._agent_key = f"wf:{ident.id}"
        loop._agent_label = ident.name
        # 编制上下文：工具权限走 steward，不向主人弹确认/提权洪水
        loop._workforce = True
        # 编制工单：不加载历史对话，避免会话膨胀首包打穿预算
        loop._workforce_skip_history = True
        loop._identity_id = str(ident.id)
        loop._identity_name = str(ident.name)
        _caps = list(ident.capabilities) if ident.capabilities is not None else []
        if _caps:
            try:
                from backend.agent.grant_store import expand_implied_tool_caps

                _caps = expand_implied_tool_caps(_caps) or _caps
            except Exception:
                pass
        loop._identity_capabilities = _caps
        _budget = self._effective_budget(
            ident, str(item.instruction or ""), item=item
        )
        try:
            from backend.kernel.llm_scheduler import map_inbox_priority

            _llm_pri = int(map_inbox_priority(getattr(item, "priority", 0)))
        except Exception:
            _llm_pri = 30
        _budget_src = "auto"
        try:
            from backend.agent.workforce_budget import resolve_job_budget

            _, _budget_src = resolve_job_budget(
                ident,
                str(item.instruction or ""),
                payload=getattr(item, "payload", None),
            )
        except Exception:
            pass
        # 编制内权限/预算注入进程创建（loop._run_inner 读取）
        _item_payload = getattr(item, "payload", None) or {}
        if not isinstance(_item_payload, dict):
            _item_payload = {}
        _owner = getattr(ident, "user_id", None)
        _owner_s = str(_owner) if _owner else None
        loop._pending_kernel_options = {
            "capabilities": list(_caps) if ident.capabilities is not None else None,
            "token_budget": _budget,
            "meta": {
                "workforce": True,
                "identity_id": str(ident.id),
                "identity_name": str(ident.name),
                "inbox_item_id": str(item.id),
                "source": str(getattr(item, "source", "") or ""),
                "cron_job_id": _item_payload.get("cron_job_id"),
                "token_budget_applied": _budget,
                "token_budget_source": _budget_src,
                "llm_source": "workforce",
                "llm_priority": _llm_pri,
                "inbox_priority": int(getattr(item, "priority", 0) or 0),
                # 多用户直接归属（与 executor 路径 create_process meta 对齐）
                "user_id": _owner_s,
                "owner_user_id": _owner_s,
            },
        }
        loop._llm_source = "workforce"
        loop._llm_priority = _llm_pri
        loop._inbox_item_id = str(item.id)
        # Phase 2.2：inbox 路径 origin；cron 投递保留 source=cron → origin=cron
        _src = str(getattr(item, "source", "") or "").strip().lower()
        loop._run_origin = "cron" if _src == "cron" else "inbox"
        # PR1–PR2: role-based max rounds (Claude max_turns) + research read-only discipline
        try:
            from backend.agent.loop_guard_bridge import (
                build_loop_guard_config,
                classify_role_kind,
            )
            from backend.agent.task_grounding import (
                classify_task,
                extra_iterations_for,
                get_spec,
            )

            _instr = str(item.instruction or "")
            _role = classify_role_kind(
                workforce=True,
                identity_name=str(getattr(ident, "name", "") or ""),
                identity_role=str(getattr(ident, "role", "") or ""),
                instruction=_instr,
                payload=_item_payload if isinstance(_item_payload, dict) else None,
            )
            _lg_cfg = build_loop_guard_config(
                workforce=True,
                identity_name=str(getattr(ident, "name", "") or ""),
                identity_role=str(getattr(ident, "role", "") or ""),
                instruction=_instr,
                payload=_item_payload if isinstance(_item_payload, dict) else None,
            )
            _mr = int(_lg_cfg.get("max_tool_rounds") or 16)
            # Python iteration budget ≈ tool rounds + 2 grace for final text
            loop.max_iterations = max(4, min(_mr + 2, 24))
            if isinstance(getattr(loop, "_pending_kernel_options", None), dict):
                _meta = loop._pending_kernel_options.setdefault("meta", {})
                if isinstance(_meta, dict):
                    _meta["loop_guard"] = _lg_cfg
                    _meta["role_kind"] = _role
                    if _lg_cfg.get("thoroughness"):
                        _meta["thoroughness"] = _lg_cfg["thoroughness"]
            if _role == "research":
                prompt += (
                    "\n【调研模式 / explore】只读取证：优先 grep/glob/file_read(offset)；"
                    f"thoroughness={_lg_cfg.get('thoroughness') or 'medium'}；"
                    f"工具轮硬顶约 {_mr}（对齐 Claude Code max_turns）。"
                    "禁止 crew_steward/delegate；禁止整文件反复重读截断结果。"
                    "到点必须交卷：结论 + 证据路径 + 未完成项。\n"
                )
            else:
                prompt += (
                    f"\n【实现工单】工具轮硬顶约 {_mr}；禁止再派工（crew_steward）。"
                    "截断读后请 offset/grep，勿整文件空转重读。\n"
                )
            _extra = extra_iterations_for(_instr)
            if _extra > 0 and _role != "research":
                loop.max_iterations = min(int(loop.max_iterations) + min(_extra, 4), 24)
                _kind = classify_task(_instr)
                _spec = get_spec(_kind)
                _label = _spec.label_zh if _spec else "取证"
                prompt += (
                    f"\n【{_label}】结论尽量可核对：路径/数字优先工具核实；"
                    "拿不准写「未核实」。\n"
                )
        except Exception:
            try:
                loop.max_iterations = min(int(getattr(loop, "max_iterations", 12) or 12), 16)
            except Exception:
                pass
        logger.info(
            "workforce run ident=%s budget=%s instr_chars=%s max_iter=%s",
            str(ident.id)[:8],
            _budget,
            len(str(item.instruction or "")),
            getattr(loop, "max_iterations", None),
        )
        result = await loop.run(session_id, prompt, attachments=None, mode="workforce")
        # Phase 2.2：工单 payload 记 run_id，便于 /runs 与 inbox 互查
        try:
            _rid = getattr(loop, "_agent_run_id", None)
            if _rid and hasattr(self._inbox, "attach_run_id"):
                await self._inbox.attach_run_id(
                    item.id, _rid, origin=getattr(loop, "_run_origin", None)
                )
        except Exception as e:
            logger.debug("inbox run_id link skipped: %s", e)
        # run() finally 会清空 _kernel_process；用 _last_kernel_process_id
        proc = getattr(loop, "_kernel_process", None)
        proc_id = None
        if proc is not None:
            proc_id = getattr(proc, "id", None) or getattr(proc, "process_id", None)
        if not proc_id:
            proc_id = getattr(loop, "_last_kernel_process_id", None)
        return (result or "(empty response)", str(proc_id) if proc_id else None)

    async def _resolve_owner_user_id(self, ident: Any) -> uuid.UUID:
        """工单会话必须挂 user_id（sessions.user_id NOT NULL）。

        与 _resolve_notify_user_id 同口径：
        identity.user_id → 单用户才回落 admin@；多用户无归属直接失败。
        """
        raw = getattr(ident, "user_id", None)
        if raw is not None:
            try:
                return raw if isinstance(raw, uuid.UUID) else uuid.UUID(str(raw))
            except (ValueError, TypeError):
                pass
        try:
            from backend.core.config import settings

            if not bool(getattr(settings, "single_user_mode", True)):
                raise RuntimeError(
                    f"员工 «{getattr(ident, 'name', '?')}» 无 user_id，"
                    "多用户模式下请先设置 identity.user_id"
                )
        except RuntimeError:
            raise
        except Exception:
            pass
        # 单用户 / 迁移窗口：admin@tevarn.dev
        try:
            from backend.repositories.user_repo import AsyncUserRepository

            u = await AsyncUserRepository().get_by_email("admin@tevarn.dev")
            if u is not None:
                return u.id
        except Exception as e:
            logger.debug("resolve default user skip: %s", e)
        raise RuntimeError(
            f"无法为员工 «{getattr(ident, 'name', '?')}» 解析 user_id，"
            "请给 Identity 设置 user_id 或启用单用户默认 admin"
        )

    async def _workforce_session_id(self, ident: Any) -> uuid.UUID:
        """身份专属 session（续作载体）：首次创建，之后复用——
        同一身份的历史对话都在一个 session 里（Episodic 上下文）。"""
        from sqlalchemy import select

        from backend.models.agent_identity import AgentIdentity

        existing = (ident.meta or {}).get("workforce_session_id")
        if existing:
            try:
                # 会话若已被删，下面 create 会重建
                sid = uuid.UUID(str(existing))
                from backend.repositories.session_repo import AsyncSessionRepository

                repo = AsyncSessionRepository()
                if await repo.get_by_id(sid) is not None:
                    return sid
            except ValueError:
                pass
            except Exception as e:
                logger.debug("workforce session reuse check: %s", e)

        from backend.repositories.session_repo import AsyncSessionRepository

        owner = await self._resolve_owner_user_id(ident)
        repo = AsyncSessionRepository()
        # AsyncSessionRepository.create 只接受 data: dict（无 title 列）
        session = await repo.create(
            {
                "user_id": owner,
                "config": {
                    "source": "workforce",
                    "contact_agent": str(getattr(ident, "name", "") or ""),
                    "identity": (
                        f"You are {ident.name}"
                        + (f", role={ident.role}" if getattr(ident, "role", None) else "")
                        + ". Complete the assigned work order carefully and report results."
                    ),
                    "workforce_identity_id": str(ident.id),
                },
            }
        )
        sid = session.id if hasattr(session, "id") else uuid.UUID(str(session))
        async with self._session_factory() as s:
            row = (
                await s.execute(select(AgentIdentity).where(AgentIdentity.id == ident.id))
            ).scalar_one_or_none()
            if row is not None:
                meta = {**(row.meta or {}), "workforce_session_id": str(sid)}
                # 顺带补上归属，避免下次再 orphan
                if row.user_id is None:
                    row.user_id = owner
                row.meta = meta
                await s.commit()
        return sid
