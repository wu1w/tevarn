"""PLAN 阶段 0.6：Agent 收件箱服务（PLAN_AI_WORKFORCE §3.f）。

cron / webhook / 邮件 / 文件变更 → 统一转工单进入身份的 inbox。
Task 只是 Agent 生命周期中的一个事件——工单就是「一封信」。

红线实现：
- 有界队列：全局 pending 超上限 → 丢弃最旧 pending（溢出策略，
  审计事件 inbox_overflow_drop），禁止无界堆积
- 身份校验：非 active 身份（suspended/archived/不存在）拒收
  （dropped + 审计），休眠语义由「无常驻进程」天然实现
- 一切投递/丢弃/完成/失败进 kernel 哈希链（process_id="identity:<uuid>"）
"""

from __future__ import annotations

import logging
import time
import uuid as _uuid
from typing import Any

from sqlalchemy import func, select

logger = logging.getLogger(__name__)

INBOX_SOURCES = ("cron", "webhook", "api", "manual")
_DEFAULT_MAX_PENDING = 200
_MAX_ATTEMPTS = 3

# audit-fix(#4)：死单 requeue 总上限。requeue(reset_attempts=True) 会清零
# attempts，没有总上限时同一死单可无限复活。AgentInboxItem 无 requeue_count
# 列（不改 schema），计数持久化在 payload["_requeue_count"]。
MAX_REQUEUE_COUNT = 2
_REQUEUE_COUNT_KEY = "_requeue_count"


def requeue_count_of(item: Any) -> int:
    """读取工单已 requeue 次数（payload JSON 内计数，缺省 0）。"""
    payload = getattr(item, "payload", None)
    if isinstance(payload, dict):
        try:
            return int(payload.get(_REQUEUE_COUNT_KEY, 0) or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def requeue_remaining_of(item: Any) -> int:
    """剩余可 requeue 次数（供失败回调 prompt 提示）。"""
    return max(0, MAX_REQUEUE_COUNT - requeue_count_of(item))


class InboxService:
    """收件箱服务。由 kernel 装配（身份事件与 kernel 事件同链）。"""

    def __init__(self, kernel: Any, session_factory: Any, *, max_pending: int = _DEFAULT_MAX_PENDING) -> None:
        self._kernel = kernel
        self._session_factory = session_factory
        self._max_pending = max(1, int(max_pending))

    def _emit(self, kind: str, identity_id: Any, detail: dict[str, Any]) -> None:
        self._kernel._emit(kind, f"identity:{identity_id}", detail)

    # ── 投递 ─────────────────────────────────────────────────

    async def enqueue(
        self,
        identity_id: Any,
        instruction: str,
        *,
        source: str = "api",
        source_ref: str | None = None,
        payload: dict[str, Any] | None = None,
        priority: int = 0,
    ) -> Any:
        """投递工单。返回 AgentInboxItem 或 None（拒收/溢出丢弃自身）。"""
        from backend.models.agent_identity import AgentIdentity, AgentInboxItem

        if source not in INBOX_SOURCES:
            raise ValueError(f"未知工单来源 {source}")
        instruction = (instruction or "").strip()
        if not instruction:
            raise ValueError("instruction 不能为空")
        iid = _uuid.UUID(str(identity_id))

        async with self._session_factory() as session:
            ident = (
                await session.execute(
                    select(AgentIdentity).where(AgentIdentity.id == iid)
                )
            ).scalar_one_or_none()
            if ident is None:
                raise ValueError(f"未知身份 {identity_id}")
            if ident.status != "active":
                self._emit("inbox_dropped", iid, {
                    "reason": f"身份状态 {ident.status}，拒收",
                    "source": source, "instruction": instruction[:200],
                })
                return None

            # 有界红线：溢出 → 丢弃最旧 pending（FIFO 淘汰）
            pending_count = (
                await session.execute(
                    select(func.count(AgentInboxItem.id)).where(
                        AgentInboxItem.status == "pending"
                    )
                )
            ).scalar_one()
            if pending_count >= self._max_pending:
                oldest = (
                    await session.execute(
                        select(AgentInboxItem)
                        .where(AgentInboxItem.status == "pending")
                        .order_by(AgentInboxItem.created_at)
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if oldest is not None:
                    oldest.status = "dropped"
                    oldest.error = "inbox 溢出（超过 max_pending）"
                    oldest.finished_at = time.time()
                    self._emit("inbox_overflow_drop", oldest.identity_id, {
                        "item_id": str(oldest.id),
                        "dropped_instruction": oldest.instruction[:200],
                        "max_pending": self._max_pending,
                    })

            item = AgentInboxItem(
                identity_id=iid,
                source=source,
                source_ref=source_ref,
                instruction=instruction,
                payload=dict(payload or {}),
                priority=priority,
            )
            session.add(item)
            await session.commit()
            await session.refresh(item)

        self._emit("inbox_enqueued", iid, {
            "item_id": str(item.id), "source": source,
            "source_ref": source_ref, "priority": priority,
            "instruction": instruction[:200],
            "identity_id": str(iid),
            "identity_name": str(getattr(ident, "name", "") or ""),
        })
        # R3: mirror to Rust claim queue (identity key + db id in meta)
        try:
            ident_name = str(getattr(ident, "name", "") or iid)
        except Exception:
            ident_name = str(iid)
        self._rust_inbox_submit(
            ident_name,
            instruction,
            priority=priority,
            db_item_id=str(item.id),
        )
        # P0: event-wake dispatcher so interactive assign is not stuck on poll sleep
        try:
            from backend.kernel.workforce import get_workforce_dispatcher

            d = get_workforce_dispatcher()
            if d is not None and hasattr(d, "nudge"):
                d.nudge()
        except Exception as _nudge_e:
            logger.debug("dispatcher nudge skip: %s", _nudge_e)
        return item

    # ── 领取与完成（Dispatcher 用）────────────────────────────

    def _rust_inbox_submit(
        self,
        identity_key: str,
        instruction: str,
        *,
        priority: int,
        db_item_id: str,
    ) -> None:
        """R3: mirror enqueue into Rust inbox for dual-worker claim coordination."""
        try:
            k = self._kernel
            if not hasattr(k, "_call"):
                return
            k._call(
                "inbox_submit",
                {
                    "identity": identity_key,
                    "instruction": instruction,
                    "priority": int(priority),
                    "meta": {"db_item_id": db_item_id},
                },
            )
        except Exception as e:
            logger.debug("rust inbox_submit skip: %s", e)

    def _rust_inbox_claim(self, worker_id: str = "dispatcher") -> dict | None:
        """R3: atomic claim from Rust host; returns dict or None."""
        try:
            k = self._kernel
            if not hasattr(k, "_call"):
                return None
            r = k._call("inbox_claim", {"worker_id": worker_id}) or {}
            if r.get("claimed") and isinstance(r.get("item"), dict):
                return r["item"]
        except Exception as e:
            logger.debug("rust inbox_claim skip: %s", e)
        return None

    def _rust_inbox_reclaim(self) -> int:
        try:
            k = self._kernel
            if not hasattr(k, "_call"):
                return 0
            r = k._call("inbox_reclaim") or {}
            return int(r.get("reclaimed") or 0)
        except Exception:
            return 0

    def _rust_host_up(self) -> bool:
        try:
            from backend.kernel_rust.client import is_rust_host_available

            return bool(is_rust_host_available())
        except Exception:
            return hasattr(self._kernel, "_call")

    def _rust_complete_by_db(self, db_item_id: str, result: str, process_id: str | None) -> None:
        try:
            k = self._kernel
            if not hasattr(k, "_call"):
                return
            k._call(
                "inbox_complete_by_db_id",
                {
                    "db_item_id": str(db_item_id),
                    "result": (result or "")[:20000],
                    "process_id": process_id,
                },
            )
        except Exception as e:
            logger.debug("rust inbox_complete_by_db skip: %s", e)

    def _rust_fail_by_db(self, db_item_id: str, reason: str) -> None:
        try:
            k = self._kernel
            if not hasattr(k, "_call"):
                return
            k._call(
                "inbox_fail_by_db_id",
                {
                    "db_item_id": str(db_item_id),
                    "reason": (reason or "failed")[:4000],
                },
            )
        except Exception as e:
            logger.debug("rust inbox_fail_by_db skip: %s", e)

    def _rust_touch_by_db(self, db_item_id: str) -> bool:
        try:
            k = self._kernel
            if not hasattr(k, "_call"):
                return False
            r = k._call("inbox_touch_by_db_id", {"db_item_id": str(db_item_id)}) or {}
            return bool(r.get("ok"))
        except Exception as e:
            logger.debug("rust inbox_touch skip: %s", e)
            return False

    def ensure_rust_pending(
        self,
        *,
        identity_key: str,
        instruction: str,
        priority: int,
        db_item_id: str,
    ) -> None:
        """Re-mirror SQL pending into Rust (host restart / requeue / reclaim)."""
        self._rust_inbox_submit(
            identity_key,
            instruction,
            priority=priority,
            db_item_id=db_item_id,
        )

    async def touch_claim(self, item_id: Any) -> bool:
        """Heartbeat claimed lease (SQL claimed_at + Rust claimed_at).

        Without this, reclaim_stale treats long-running workers as dead and
        drops claimed→pending (sticky fail).
        """
        from backend.models.agent_identity import AgentInboxItem

        now = time.time()
        ok = False
        async with self._session_factory() as session:
            item = (
                await session.execute(
                    select(AgentInboxItem).where(
                        AgentInboxItem.id == _uuid.UUID(str(item_id))
                    )
                )
            ).scalar_one_or_none()
            if item is None or item.status != "claimed":
                return False
            item.claimed_at = now
            await session.commit()
            ok = True
        if ok:
            self._rust_touch_by_db(str(item_id))
        return ok

    async def claim_next(self, *, busy_identity_ids: set[str] | None = None) -> Any:
        """领取下一条工单（优先级降序 + FIFO）。同一身份同时在手一单
        （编制内串行——一个员工不能同时干两单活）。

        R3：host 在线时 **只走** Rust inbox_claim → 原子 UPDATE DB；
        禁止静默纯 SQL claim（双 worker 竞态）。host 宕机 + DEV_UNSAFE 才回落。
        """
        from sqlalchemy import update

        from backend.models.agent_identity import AgentIdentity, AgentInboxItem

        busy = {_uuid.UUID(str(x)) for x in (busy_identity_ids or set())}

        # ── Rust-first claim coordination ──
        rust_item = self._rust_inbox_claim(worker_id=f"disp-{id(self) % 10000}")
        if rust_item is not None:
            meta = rust_item.get("meta") if isinstance(rust_item.get("meta"), dict) else {}
            db_id = str(meta.get("db_item_id") or "")
            claim_token = str(rust_item.get("claim_token") or "")
            rust_qid = str(rust_item.get("id") or "")
            item_uuid = None
            if db_id:
                try:
                    item_uuid = _uuid.UUID(db_id)
                except Exception:
                    item_uuid = None
            if item_uuid is not None:
                async with self._session_factory() as session:
                    item = (
                        await session.execute(
                            select(AgentInboxItem).where(AgentInboxItem.id == item_uuid)
                        )
                    ).scalar_one_or_none()
                    if item is not None and item.status == "pending":
                        if item.identity_id in busy:
                            try:
                                if hasattr(self._kernel, "_call") and claim_token:
                                    self._kernel._call(
                                        "inbox_release",
                                        {
                                            "item_id": rust_qid,
                                            "claim_token": claim_token,
                                        },
                                    )
                            except Exception:
                                pass
                        else:
                            now = time.time()
                            result = await session.execute(
                                update(AgentInboxItem)
                                .where(
                                    AgentInboxItem.id == item.id,
                                    AgentInboxItem.status == "pending",
                                )
                                .values(
                                    status="claimed",
                                    claimed_at=now,
                                    attempts=AgentInboxItem.attempts + 1,
                                )
                            )
                            if result.rowcount == 1:
                                await session.commit()
                                await session.refresh(item)
                                self._emit(
                                    "inbox_claimed",
                                    item.identity_id,
                                    {
                                        "item_id": str(item.id),
                                        "attempts": item.attempts,
                                        "via": "rust",
                                    },
                                )
                                return item
                    # rust claimed but DB miss / already claimed → release rust lease
                    try:
                        if hasattr(self._kernel, "_call") and claim_token:
                            self._kernel._call(
                                "inbox_release",
                                {
                                    "item_id": rust_qid,
                                    "claim_token": claim_token,
                                },
                            )
                    except Exception:
                        pass

        # host 在线：纯 SQL claim 禁止（双 worker 必须走 Rust 协调）
        if self._rust_host_up():
            return None

        # DEV / host-down fallback only
        try:
            from backend.kernel.production_guard import is_dev_unsafe

            if not is_dev_unsafe():
                logger.warning(
                    "inbox claim_next: rust host down — refuse pure-SQL claim "
                    "(set TAKTON_DEV_UNSAFE=1 for local fallback)"
                )
                return None
        except Exception:
            logger.warning("inbox claim_next: rust host down, refuse pure-SQL claim")
            return None

        async with self._session_factory() as session:
            # 防双派：DB 层已 claimed 的身份也不可再领新单（多 worker / busy 集合丢失兜底）
            claimed_idents: set[_uuid.UUID] = set()
            try:
                claimed_rows = (
                    (
                        await session.execute(
                            select(AgentInboxItem.identity_id).where(
                                AgentInboxItem.status == "claimed"
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                claimed_idents = {x for x in claimed_rows if x is not None}
            except Exception:
                claimed_idents = set()

            candidates = (
                (
                    await session.execute(
                        select(AgentInboxItem)
                        .where(AgentInboxItem.status == "pending")
                        .order_by(AgentInboxItem.priority.desc(), AgentInboxItem.created_at)
                        .limit(20)
                    )
                )
                .scalars()
                .all()
            )
            for item in candidates:
                if item.identity_id in busy:
                    continue
                if item.identity_id in claimed_idents:
                    continue
                # 身份仍 active 才派发；suspended 的挂起等待（不丢）
                ident = (
                    await session.execute(
                        select(AgentIdentity.status).where(AgentIdentity.id == item.identity_id)
                    )
                ).scalar_one_or_none()
                if ident != "active":
                    continue
                now = time.time()
                result = await session.execute(
                    update(AgentInboxItem)
                    .where(
                        AgentInboxItem.id == item.id,
                        AgentInboxItem.status == "pending",
                    )
                    .values(
                        status="claimed",
                        claimed_at=now,
                        attempts=AgentInboxItem.attempts + 1,
                    )
                )
                if result.rowcount != 1:
                    continue  # 被并发抢走
                await session.commit()
                await session.refresh(item)
                self._emit("inbox_claimed", item.identity_id, {
                    "item_id": str(item.id), "attempts": item.attempts,
                    "via": "sql_fallback_dev_unsafe",
                })
                return item
        return None

    async def reclaim_stale_claims(
        self,
        *,
        timeout_seconds: float = 600.0,
        busy_identity_ids: set[str] | None = None,
        live_item_ids: set[str] | None = None,
        orphan_grace_seconds: float = 45.0,
        force_all_orphans: bool = False,
    ) -> int:
        """回收超时仍停留在 claimed 的工单（worker 崩溃/超时未 fail）。

        回到 pending 并保留 attempts，由 fail 路径负责达上限转 failed。
        返回回收条数。同时触发 Rust inbox_reclaim。

        busy_identity_ids: dispatcher 内存中正在跑的身份 — **绝不 reclaim**
        （避免长任务 heartbeat 间隙被误回收）。

        live_item_ids: 本进程正在跑的工单 id。不在集合中且超过 orphan_grace
        的 claimed 视为孤儿（后端重启后常见：status=claimed 但 worker 已死）。
        force_all_orphans=True：启动时强制回收全部非 live claimed。
        """
        from backend.models.agent_identity import AgentInboxItem

        busy = {str(x) for x in (busy_identity_ids or set())}
        live_items = {str(x) for x in (live_item_ids or set())}

        try:
            self._rust_inbox_reclaim()
        except Exception:
            pass

        now = time.time()
        cutoff = now - max(30.0, float(timeout_seconds))
        orphan_cutoff = now - max(15.0, float(orphan_grace_seconds))
        n = 0
        requeued: list[tuple[Any, str, str, int]] = []
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(AgentInboxItem).where(AgentInboxItem.status == "claimed")
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                iid = str(row.identity_id)
                if iid in busy:
                    continue
                item_key = str(row.id)
                # 本进程仍在跑 → 跳过
                if item_key in live_items:
                    continue
                claimed_at = float(row.claimed_at or 0)
                is_orphan = force_all_orphans or (
                    claimed_at > 0 and claimed_at <= orphan_cutoff
                )
                is_timeout = claimed_at > 0 and claimed_at <= cutoff
                # claimed_at 缺失/异常：也当孤儿（防永久卡死）
                if claimed_at <= 0:
                    is_orphan = True
                if not (is_timeout or is_orphan):
                    continue
                why = (
                    "startup_orphan"
                    if force_all_orphans
                    else (
                        "orphan_no_live_worker"
                        if is_orphan and not is_timeout
                        else f"timeout_{timeout_seconds:.0f}s"
                    )
                )
                row.status = "pending"
                row.error = (
                    (row.error or "")[:3500]
                    + f" | reclaimed ({why})"
                )
                # Capture for rust re-mirror after commit
                try:
                    from backend.models.agent_identity import AgentIdentity

                    ident = (
                        await session.execute(
                            select(AgentIdentity).where(AgentIdentity.id == row.identity_id)
                        )
                    ).scalar_one_or_none()
                    ikey = str(getattr(ident, "name", "") or iid)
                except Exception:
                    ikey = iid
                requeued.append(
                    (
                        str(row.id),
                        ikey,
                        str(row.instruction or ""),
                        int(row.priority or 0),
                    )
                )
                self._emit("inbox_reclaimed", row.identity_id, {
                    "item_id": str(row.id),
                    "claimed_at": claimed_at,
                    "timeout_seconds": timeout_seconds,
                })
                n += 1
            if n:
                await session.commit()
        # Re-mirror to Rust so digestion continues after reclaim
        for db_id, ikey, instr, prio in requeued:
            try:
                self.ensure_rust_pending(
                    identity_key=ikey,
                    instruction=instr,
                    priority=prio,
                    db_item_id=db_id,
                )
            except Exception:
                pass
        return n

    async def attach_run_id(self, item_id: Any, run_id: Any, *, origin: str | None = None) -> None:
        """Phase 2.2：工单 payload 关联 AgentRun id（/runs 互查）。"""
        from backend.models.agent_identity import AgentInboxItem

        async with self._session_factory() as session:
            item = (
                await session.execute(
                    select(AgentInboxItem).where(AgentInboxItem.id == _uuid.UUID(str(item_id)))
                )
            ).scalar_one_or_none()
            if item is None:
                return
            payload = dict(item.payload or {}) if isinstance(item.payload, dict) else {}
            payload["run_id"] = str(run_id)
            if origin:
                payload["origin"] = str(origin)
            item.payload = payload
            await session.commit()

    async def release_claim_to_pending(
        self, item_id: Any, *, reason: str = "busy_race"
    ) -> bool:
        """将 claimed 工单退回 pending（Redis busy 抢占失败时用）。"""
        from sqlalchemy import update

        from backend.models.agent_identity import AgentInboxItem

        async with self._session_factory() as session:
            result = await session.execute(
                update(AgentInboxItem)
                .where(
                    AgentInboxItem.id == _uuid.UUID(str(item_id)),
                    AgentInboxItem.status == "claimed",
                )
                .values(status="pending", claimed_at=None)
            )
            await session.commit()
            ok = int(result.rowcount or 0) == 1
        if ok:
            # Dual-write: free Rust claim slot (was SQL-only → sticky desync)
            self._rust_fail_by_db(str(item_id), f"release:{reason[:80]}")
            try:
                self._emit(
                    "inbox_requeued",
                    item_id,
                    {"item_id": str(item_id), "reason": reason[:120]},
                )
            except Exception:
                pass
        return ok

    async def complete(self, item_id: Any, result: str, *, process_id: str | None = None) -> None:
        from backend.models.agent_identity import AgentInboxItem

        async with self._session_factory() as session:
            item = (
                await session.execute(
                    select(AgentInboxItem).where(AgentInboxItem.id == _uuid.UUID(str(item_id)))
                )
            ).scalar_one_or_none()
            if item is None:
                return
            item.status = "done"
            item.result = (result or "")[:20000]
            item.process_id = process_id
            item.finished_at = time.time()
            identity_id = item.identity_id
            instruction = str(item.instruction or "")
            await session.commit()
        # Dual-complete Rust claim queue (authority for multi-worker)
        self._rust_complete_by_db(str(item_id), result or "", process_id)
        _iname = ""
        try:
            from backend.models.agent_identity import AgentIdentity as _AI

            async with self._session_factory() as _s:
                _row = (
                    await _s.execute(
                        select(_AI).where(_AI.id == identity_id)
                    )
                ).scalar_one_or_none()
                if _row is not None:
                    _iname = str(getattr(_row, "name", "") or "")
        except Exception:
            pass
        self._emit("inbox_done", identity_id, {
            "item_id": str(item_id),
            "process_id": process_id,
            "identity_id": str(identity_id),
            "identity_name": _iname,
        })
        # P0-4: 工单完成 → 自动沉淀 experience（成长轨迹）
        try:
            from backend.kernel.experience_sink import record_job_experience

            await record_job_experience(
                identity_id=identity_id,
                instruction=instruction,
                result=result or "",
                process_id=process_id,
                status="done",
            )
        except Exception:
            pass

    async def fail(
        self,
        item_id: Any,
        error: str,
        *,
        process_id: str | None = None,
        result: str | None = None,
        terminal: bool = False,
    ) -> None:
        """失败处理：attempts 未达上限 → pending 重试；达上限 → dead。

        terminal=True：直接终态 failed（不自动重试），用于预算耗尽。
        result：可选写入 result，便于侧栏展示 Budget 文案。
        """
        from backend.models.agent_identity import AgentInboxItem

        async with self._session_factory() as session:
            item = (
                await session.execute(
                    select(AgentInboxItem).where(AgentInboxItem.id == _uuid.UUID(str(item_id)))
                )
            ).scalar_one_or_none()
            if item is None:
                return
            identity_id = item.identity_id
            attempts = item.attempts
            err = (error or "")[:4000]
            if result:
                item.result = (result or "")[:20000]
            item.process_id = process_id
            item.error = err
            if terminal:
                item.status = "failed"
                item.finished_at = time.time()
                event = "inbox_dead"
            elif attempts >= _MAX_ATTEMPTS:
                item.status = "dead"
                item.finished_at = time.time()
                event = "inbox_dead"
            else:
                item.status = "pending"
                event = "inbox_retry"
            instr = str(item.instruction or "")
            prio = int(item.priority or 0)
            await session.commit()
        # Dual-write Rust: free claim slot (retry or terminal)
        # Leaving rust claimed on non-terminal fail blocked max_claimed_per_identity.
        self._rust_fail_by_db(str(item_id), err)
        if event == "inbox_retry":
            # Re-mirror pending so host can claim again immediately
            try:
                from backend.models.agent_identity import AgentIdentity

                async with self._session_factory() as s2:
                    ident = (
                        await s2.execute(
                            select(AgentIdentity).where(
                                AgentIdentity.id == identity_id
                            )
                        )
                    ).scalar_one_or_none()
                ikey = str(getattr(ident, "name", "") or identity_id)
                self.ensure_rust_pending(
                    identity_key=ikey,
                    instruction=instr,
                    priority=prio,
                    db_item_id=str(item_id),
                )
            except Exception as e:
                logger.debug("fail→retry rust resubmit skip: %s", e)
        self._emit(
            event,
            identity_id,
            {
                "item_id": str(item_id),
                "attempts": attempts,
                "error": (error or "")[:300],
                "terminal": terminal,
                "identity_id": str(identity_id),
                "process_id": process_id,
            },
        )

    async def cancel(
        self,
        item_id: Any,
        *,
        reason: str = "cancelled by user",
        process_id: str | None = None,
    ) -> Any | None:
        """E4 停止语义：pending/claimed 工单 → cancelled（不重试、不进死信）。

        已是终态（done/dead/failed/dropped/cancelled）则幂等返回原行。
        """
        from backend.models.agent_identity import AgentInboxItem

        async with self._session_factory() as session:
            item = (
                await session.execute(
                    select(AgentInboxItem).where(AgentInboxItem.id == _uuid.UUID(str(item_id)))
                )
            ).scalar_one_or_none()
            if item is None:
                return None
            terminal = {"done", "dead", "failed", "dropped", "cancelled"}
            if item.status in terminal:
                return item
            item.status = "cancelled"
            item.error = (reason or "cancelled")[:4000]
            if process_id:
                item.process_id = process_id
            item.finished_at = time.time()
            identity_id = item.identity_id
            await session.commit()
            await session.refresh(item)
        self._emit(
            "inbox_cancelled",
            identity_id,
            {
                "item_id": str(item_id),
                "process_id": process_id,
                "reason": (reason or "")[:300],
            },
        )
        return item

    async def requeue(
        self,
        item_id: Any,
        *,
        reset_attempts: bool = True,
    ) -> Any | None:
        """死信/失败工单重放 → pending。"""
        from backend.models.agent_identity import AgentInboxItem

        async with self._session_factory() as session:
            item = (
                await session.execute(
                    select(AgentInboxItem).where(AgentInboxItem.id == _uuid.UUID(str(item_id)))
                )
            ).scalar_one_or_none()
            if item is None:
                return None
            if item.status not in ("dead", "failed", "dropped"):
                return item  # 已在途/完成则不改
            # audit-fix(#4)：requeue 总上限——超限转 dead 并在结果里注明，
            # 防止 reset_attempts 清零后死单无限复活
            _rq = requeue_count_of(item)
            if _rq >= MAX_REQUEUE_COUNT:
                item.status = "dead"
                _note = (
                    f"[requeue-cap] requeue 次数已用尽（上限 {MAX_REQUEUE_COUNT}），"
                    "工单转 dead，需人工介入排查后手动处理"
                )
                item.error = ((item.error or "") + ("\n" if item.error else "") + _note)[
                    :4000
                ]
                item.result = ((item.result or "") + ("\n" if item.result else "") + _note)[
                    :4000
                ]
                item.finished_at = time.time()
                await session.commit()
                await session.refresh(item)
                self._emit(
                    "inbox_requeue_capped",
                    item.identity_id,
                    {"item_id": str(item.id), "requeue_count": _rq},
                )
                return item
            item.status = "pending"
            if reset_attempts:
                item.attempts = 0
            # 计数 +1（重新赋值整 dict 以触发 SQLAlchemy JSON 变更检测）
            item.payload = {
                **(item.payload if isinstance(item.payload, dict) else {}),
                _REQUEUE_COUNT_KEY: _rq + 1,
            }
            item.error = None
            item.result = None
            item.process_id = None
            item.claimed_at = None
            item.finished_at = None
            await session.commit()
            await session.refresh(item)
            # Re-mirror to Rust so host can claim again after dead/failed requeue
            try:
                from backend.models.agent_identity import AgentIdentity

                async with self._session_factory() as s2:
                    ident = (
                        await s2.execute(
                            select(AgentIdentity).where(
                                AgentIdentity.id == item.identity_id
                            )
                        )
                    ).scalar_one_or_none()
                ikey = str(getattr(ident, "name", "") or item.identity_id)
                self.ensure_rust_pending(
                    identity_key=ikey,
                    instruction=str(item.instruction or ""),
                    priority=int(item.priority or 0),
                    db_item_id=str(item.id),
                )
            except Exception as e:
                logger.debug("requeue rust resubmit skip: %s", e)
            self._emit("inbox_requeued", item.identity_id, {"item_id": str(item.id)})
            return item

    async def discard_dead(self, item_id: Any) -> bool:
        """丢弃死信（标记 dropped，保留审计痕迹）。"""
        from backend.models.agent_identity import AgentInboxItem

        async with self._session_factory() as session:
            item = (
                await session.execute(
                    select(AgentInboxItem).where(AgentInboxItem.id == _uuid.UUID(str(item_id)))
                )
            ).scalar_one_or_none()
            if item is None:
                return False
            if item.status not in ("dead", "failed"):
                return False
            item.status = "dropped"
            item.finished_at = time.time()
            await session.commit()
            self._emit("inbox_discarded", item.identity_id, {"item_id": str(item_id)})
            return True

    # ── 查询（日报/控制台用）──────────────────────────────────

    async def list_items(
        self,
        *,
        identity_id: Any | None = None,
        status: str | None = None,
        limit: int = 50,
        since_ts: float | None = None,
    ) -> list[Any]:
        from backend.models.agent_identity import AgentInboxItem

        async with self._session_factory() as session:
            q = select(AgentInboxItem).order_by(AgentInboxItem.created_at.desc()).limit(limit)
            if identity_id is not None:
                q = q.where(AgentInboxItem.identity_id == _uuid.UUID(str(identity_id)))
            if status is not None:
                q = q.where(AgentInboxItem.status == status)
            if since_ts is not None:
                from datetime import datetime, timezone

                q = q.where(
                    AgentInboxItem.created_at >= datetime.fromtimestamp(since_ts, tz=timezone.utc)
                )
            return list((await session.execute(q)).scalars().all())

    async def stats(self, *, since_ts: float | None = None) -> dict[str, int]:
        from backend.models.agent_identity import AgentInboxItem

        async with self._session_factory() as session:
            q = select(AgentInboxItem.status, func.count()).group_by(AgentInboxItem.status)
            if since_ts is not None:
                from datetime import datetime, timezone

                q = q.where(
                    AgentInboxItem.created_at >= datetime.fromtimestamp(since_ts, tz=timezone.utc)
                )
            rows = (await session.execute(q)).all()
            return {str(status): int(count) for status, count in rows}
