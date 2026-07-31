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

    async def run_forever(self) -> None:
        """后台主循环（lifespan _spawn_bg 拉起）。"""
        self._running = True
        logger.info("workforce dispatcher started (poll=%.0fs)", self._poll_seconds)
        while self._running:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("dispatcher tick 失败（下一轮继续）: %s", e)
            try:
                await asyncio.sleep(self._poll_seconds)
            except asyncio.CancelledError:
                raise

    async def stop(self) -> None:
        self._running = False

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
            if source == "ceo":
                logger.info(
                    "job budget ceo-override identity=%s budget=%s",
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
        """Dispatcher tick: Rust reclaim + isolation OS reap (best-effort)."""
        k = self._kernel
        if not hasattr(k, "_call"):
            return
        try:
            k._call("inbox_reclaim")
        except Exception as e:
            logger.debug("rust inbox_reclaim: %s", e)
        try:
            timeout = float(
                getattr(settings, "agent_inbox_item_timeout", self._item_timeout)
                or self._item_timeout
            )
            k._call("isolation_reap", {"max_age_secs": max(timeout, 60.0)})
        except Exception as e:
            logger.debug("rust isolation_reap: %s", e)

    def _identity_admit(self, ident: Any) -> bool:
        """Register + admit identity concurrency slot in Rust authority."""
        k = self._kernel
        if not hasattr(k, "_call"):
            return True
        try:
            iid = str(getattr(ident, "id", "") or "")
            name = str(getattr(ident, "name", "") or iid)
            role = str(getattr(ident, "role", "") or "")
            caps = list(getattr(ident, "capabilities", None) or [])
            # ensure identity exists in rust cache
            k._call(
                "identity_hire",
                {
                    "id": iid,
                    "name": name,
                    "role": role,
                    "capabilities": caps,
                    "max_concurrent": 1,
                },
            )
            r = k._call("identity_admit", {"id": name}) or k._call(
                "identity_admit", {"id": iid}
            )
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
        k = self._kernel
        if not hasattr(k, "_call") or ident is None:
            return
        try:
            iid = str(getattr(ident, "id", "") or "")
            name = str(getattr(ident, "name", "") or iid)
            k._call("identity_release", {"id": name})
            if iid and iid != name:
                k._call("identity_release", {"id": iid})
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
        # 回收超时 claimed（worker 崩溃残留）— SQL mirror + rust
        try:
            timeout = float(getattr(settings, "agent_inbox_item_timeout", self._item_timeout) or self._item_timeout)
            await self._inbox.reclaim_stale_claims(timeout_seconds=timeout)
        except Exception as e:
            logger.debug("reclaim_stale_claims skip: %s", e)
        dispatched = 0
        tasks: list[asyncio.Task] = []
        max_global = self._max_global_concurrent()
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

    async def _notify_owner(
        self,
        *,
        kind: str,
        title: str,
        content: str,
        identity: Any | None = None,
        item_id: Any | None = None,
    ) -> None:
        """工单完成/失败时通知主人（单用户取 identity.user_id 或默认 admin）。"""
        try:
            from backend.repositories.notification_repo import (
                AsyncNotificationRepository,
            )
            from backend.repositories.user_repo import AsyncUserRepository

            uid = getattr(identity, "user_id", None) if identity is not None else None
            if uid is None:
                u = await AsyncUserRepository().get_by_email("admin@takton.dev")
                uid = u.id if u is not None else None
            if uid is None:
                return
            repo = AsyncNotificationRepository()
            await repo.create(
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
                        "source": "workforce_dispatcher",
                    },
                    "source_id": str(item_id or "")[:64] or None,
                }
            )
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
                nm = names.get(str(it.identity_id), str(it.identity_id)[:8])
                body = (it.result or it.error or "").strip() or "（无正文）"
                if len(body) > 4500:
                    body = body[:4500] + "\n…[truncated]"
                blocks.append(
                    f"### [{st}] {nm}\n"
                    f"任务：{(it.instruction or '')[:400]}\n\n"
                    f"结果：\n{body}\n"
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
            owner = await self._resolve_owner_user_id(identity) if identity is not None else None
        except Exception:
            owner = None
        if owner is None:
            try:
                from backend.repositories.user_repo import AsyncUserRepository

                u = await AsyncUserRepository().get_by_email("admin@takton.dev")
                owner = u.id if u else None
            except Exception:
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
        prompt = (
            f"【系统·编制自动回调】你派发的「{title}」相关工单已全部结束"
            f"（触发员工：{name or '—'}）。\n"
            f"{honesty}"
            "请**立即**把下列结果汇总成主人可读的中文汇报：\n"
            "1. 总结论（须与批次状态一致）\n"
            "2. 分员工要点（按 [done]/[failed]/[dead] 标签）\n"
            "3. 风险与建议下一步（失败项如何重派/抬预算/拆单）\n"
            "可用 crew_steward action=results 再核对，但**禁止**再次 hire/assign 同一批任务。\n\n"
            + "\n".join(blocks)
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
            # 汇总轮不宜太长
            try:
                loop.max_iterations = min(int(getattr(loop, "max_iterations", 12) or 12), 12)
            except Exception:
                pass
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

                async def _hb() -> None:
                    interval = max(30.0, min(120.0, self._busy_ttl_seconds() / 4))
                    while not hb_stop.is_set():
                        try:
                            await asyncio.wait_for(hb_stop.wait(), timeout=interval)
                            break
                        except asyncio.TimeoutError:
                            self._refresh_redis_busy(str(item.identity_id), item_key)

                hb_task = asyncio.create_task(_hb())
                try:
                    await self._run_item(item, proc_id_holder=proc_id_holder)
                finally:
                    hb_stop.set()
                    try:
                        await hb_task
                    except Exception:
                        pass

            await asyncio.wait_for(
                _run_with_busy_heartbeat(), timeout=self._item_timeout
            )
            # 尽量带上 result 摘要（完成正文在 DB；item 内存可能无 result）
            result_snip = await self._load_item_result_snip(item.id)
            await self._notify_owner(
                kind="task_complete",
                title=f"工单完成 · {getattr(ident, 'name', '员工')}",
                content=(
                    (result_snip or "")[:1800]
                    or (item.instruction or "")[:200]
                ),
                identity=ident,
                item_id=item.id,
            )
            # 批次全部结束后唤醒 CEO 会话汇总（异步，不挡 dispatcher）
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

    async def _run_item(self, item: Any, *, proc_id_holder: dict | None = None) -> None:
        """唤醒身份执行一单。全程 kernel 中介 + 预算扣减。

        进程归属：executor 路径由 dispatcher 建进程（审计锚点）；
        生产 loop 路径由 loop._run_inner 建进程（带编制选项），
        dispatcher 回读进程 id 关联工单。
        """
        ident = await self._registry.get(item.identity_id)
        if ident is None or ident.status != "active":
            await self._inbox.fail(item.id, "身份不存在或已停用")
            return

        # Rust identity admit (concurrency authority) before waking worker
        if not self._identity_admit(ident):
            try:
                await self._inbox.release_claim_to_pending(
                    item.id, reason="identity_admit_denied"
                )
            except Exception:
                await self._inbox.fail(item.id, "identity admit denied (at capacity)")
            return

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
            kernel_proc = await self._kernel.create_process(
                f"wf:{ident.id}",
                session_id=None,
                capabilities=list(ident.capabilities) if ident.capabilities is not None else None,
                token_budget=_budget,
                meta={"inbox_item_id": str(item.id), "identity_id": str(ident.id),
                      "source": item.source, "identity_name": getattr(ident, "name", None),
                      "token_budget_applied": _budget},
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
            await self._finish_item(item, result, process_id=kernel_proc.id)
            return

        result, proc_id = await self._execute_with_loop(ident, item, item_key=item_key)
        if proc_id_holder is not None:
            proc_id_holder["id"] = proc_id
        if proc_id:
            self._item_proc_ids[item_key] = str(proc_id)
            self._proc_to_item[str(proc_id)] = item_key
        await self._finish_item(item, result, process_id=proc_id)

    async def _finish_item(
        self, item: Any, result: str, *, process_id: str | None
    ) -> None:
        """Budget 中断 → fail（可重试/进死信）；正常 → complete。"""
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
            return

        if process_id:
            try:
                await self._kernel.end_process(process_id, state="completed")
            except Exception:
                pass
        await self._inbox.complete(item.id, text, process_id=process_id)

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
        loop._identity_capabilities = (
            list(ident.capabilities) if ident.capabilities is not None else []
        )
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
        loop._kernel_process_options = {
            "capabilities": list(ident.capabilities) if ident.capabilities is not None else None,
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
            },
        }
        loop._llm_source = "workforce"
        loop._llm_priority = _llm_pri
        loop._inbox_item_id = str(item.id)
        # Phase 2.2：inbox 路径 origin；cron 投递保留 source=cron → origin=cron
        _src = str(getattr(item, "source", "") or "").strip().lower()
        loop._run_origin = "cron" if _src == "cron" else "inbox"
        # 落地类工单（审计/检索/数据/…）提高迭代 + 轻提示
        try:
            from backend.agent.task_grounding import (
                classify_task,
                extra_iterations_for,
                get_spec,
            )

            _instr = str(item.instruction or "")
            _base = int(getattr(loop, "max_iterations", 12) or 12)
            _extra = extra_iterations_for(_instr)
            if _extra > 0:
                loop.max_iterations = min(max(_base, 12 + _extra), 28)
                _kind = classify_task(_instr)
                _spec = get_spec(_kind)
                _label = _spec.label_zh if _spec else "取证"
                prompt += (
                    f"\n【{_label}】结论尽量可核对：路径/数字优先工具核实；"
                    "拿不准写「未核实」。\n"
                )
            else:
                loop.max_iterations = min(_base, 16)
        except Exception:
            pass
        logger.info(
            "workforce run ident=%s budget=%s instr_chars=%s",
            str(ident.id)[:8],
            _budget,
            len(str(item.instruction or "")),
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

        优先身份归属；缺失时（旧 hire / manage_sub_agent 双写）回落单用户默认 admin。
        """
        raw = getattr(ident, "user_id", None)
        if raw is not None:
            try:
                return raw if isinstance(raw, uuid.UUID) else uuid.UUID(str(raw))
            except (ValueError, TypeError):
                pass
        # 单用户 / 迁移窗口：admin@takton.dev
        try:
            from backend.repositories.user_repo import AsyncUserRepository

            u = await AsyncUserRepository().get_by_email("admin@takton.dev")
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
