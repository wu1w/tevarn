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
        })
        return item

    # ── 领取与完成（Dispatcher 用）────────────────────────────

    async def claim_next(self, *, busy_identity_ids: set[str] | None = None) -> Any:
        """领取下一条工单（优先级降序 + FIFO）。同一身份同时在手一单
        （编制内串行——一个员工不能同时干两单活）。

        原子 claim：`UPDATE … WHERE status='pending'` 行数=1 才算领到，
        防止多 dispatcher/worker 双派同一单。
        """
        from sqlalchemy import update

        from backend.models.agent_identity import AgentIdentity, AgentInboxItem

        busy = {_uuid.UUID(str(x)) for x in (busy_identity_ids or set())}
        async with self._session_factory() as session:
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
                })
                return item
        return None

    async def reclaim_stale_claims(self, *, timeout_seconds: float = 600.0) -> int:
        """回收超时仍停留在 claimed 的工单（worker 崩溃/超时未 fail）。

        回到 pending 并保留 attempts，由 fail 路径负责达上限转 failed。
        返回回收条数。
        """
        from backend.models.agent_identity import AgentInboxItem

        cutoff = time.time() - max(30.0, float(timeout_seconds))
        n = 0
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
                claimed_at = float(row.claimed_at or 0)
                if claimed_at <= 0 or claimed_at > cutoff:
                    continue
                row.status = "pending"
                row.error = (row.error or "")[:3500] + f" | reclaimed after {timeout_seconds:.0f}s claim timeout"
                self._emit("inbox_reclaimed", row.identity_id, {
                    "item_id": str(row.id),
                    "claimed_at": claimed_at,
                    "timeout_seconds": timeout_seconds,
                })
                n += 1
            if n:
                await session.commit()
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
        self._emit("inbox_done", identity_id, {
            "item_id": str(item_id), "process_id": process_id,
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
            await session.commit()
        self._emit(
            event,
            identity_id,
            {
                "item_id": str(item_id),
                "attempts": attempts,
                "error": (error or "")[:300],
                "terminal": terminal,
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
            item.status = "pending"
            if reset_attempts:
                item.attempts = 0
            item.error = None
            item.result = None
            item.process_id = None
            item.claimed_at = None
            item.finished_at = None
            await session.commit()
            await session.refresh(item)
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
