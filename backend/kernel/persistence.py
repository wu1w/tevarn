"""0.5 编制与档案：kernel 持久化层（PLAN_AI_WORKFORCE §3.a/3.b）。

设计约束（kernel 单线程红线）：kernel 所有 public 方法内部零 await，
而落盘是 IO——因此采用 **sink 模式**：
- kernel 同步侧把持久化操作 put_nowait 进 asyncio.Queue（同步调用，零 await）
- 异步消费者（worker 后台任务 / 测试中的 flush）从队列取出并写 DB

持久化内容：
- 进程档案（kernel_processes 表）：create/end/state/capabilities 变更时 upsert
- checkpoint（kernel_checkpoints 表）：每 interval 个事件写一次快照
  （事件本体已由 audit_store JSONL 落盘 = event store，不重复入 DB）
- 启动恢复：created/running 进程标记 interrupted（诚实中断，不伪造存活）；
  恢复路径 = 最新快照 + tail_hash 之后的增量事件（禁止全量 replay）

失败策略：落盘失败只告警不阻断（与 audit_store 一致——持久化是增强，不是单点）。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from sqlalchemy import select, update

logger = logging.getLogger(__name__)

_DEFAULT_CHECKPOINT_INTERVAL = 500


class KernelPersistence:
    """kernel 持久化协调器。sink() 返回的回调供 kernel 同步侧调用。"""

    def __init__(
        self,
        session_factory: Any = None,
        audit_store: Any = None,
        *,
        checkpoint_interval: int = _DEFAULT_CHECKPOINT_INTERVAL,
    ) -> None:
        self._session_factory = session_factory
        self._audit_store = audit_store
        self._checkpoint_interval = max(1, int(checkpoint_interval))
        # audit-fix: 有界队列（maxsize=10000），防消费端落后时内存无界增长
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=10000)
        self._events_since_checkpoint = 0
        self._total_events = 0

    def sink(self) -> Callable[[dict[str, Any]], None]:
        """kernel 同步侧挂载点（put_nowait 是同步方法，零 await）。"""
        q = self._queue

        def _put(op: dict[str, Any]) -> None:
            try:
                q.put_nowait(op)
            except asyncio.QueueFull:
                # audit-fix: 队列满时丢弃最旧一条再放（持久化是增强不是单点）
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(op)
                except asyncio.QueueFull:
                    pass
                logger.warning(
                    "kernel persistence queue full (maxsize=%s)：丢弃最旧操作",
                    q.maxsize,
                )

        return _put

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    # ── 异步消费 ─────────────────────────────────────────────

    async def flush(self, *, limit: int = 10000) -> int:
        """排空队列（测试与优雅关闭用）。返回处理数。"""
        n = 0
        while n < limit:
            try:
                op = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            await self._apply(op)
            n += 1
        return n

    async def worker(self, poll_interval: float = 0.5) -> None:
        """后台消费循环（生产环境由 app lifespan 拉起）。"""
        while True:
            try:
                op = await asyncio.wait_for(self._queue.get(), timeout=poll_interval)
                await self._apply(op)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                await self.flush()
                raise

    async def _apply(self, op: dict[str, Any]) -> None:
        try:
            kind = op.get("op")
            if kind == "process_upsert":
                await self._upsert_process(op["data"])
            elif kind == "escalation_upsert":
                await self._upsert_escalation(op["data"])
            elif kind == "event":
                self._total_events += 1
                self._events_since_checkpoint += 1
                if self._events_since_checkpoint >= self._checkpoint_interval:
                    await self.write_checkpoint(tail_hash=str(op.get("data", {}).get("hash") or ""))
        except Exception as e:
            logger.warning("kernel 持久化失败（不阻断）op=%s: %s", op.get("op"), e)

    async def _upsert_escalation(self, data: dict[str, Any]) -> None:
        """提权申请外部化落盘（多 worker 可读 pending）。"""
        if self._session_factory is None:
            return
        from backend.models.agent_identity import KernelEscalationRecord

        eid = str(data.get("id") or data.get("escalation_id") or "")
        if not eid:
            return
        async with self._session_factory() as session:
            existing = (
                await session.execute(
                    select(KernelEscalationRecord).where(
                        KernelEscalationRecord.escalation_id == eid
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                session.add(KernelEscalationRecord(
                    escalation_id=eid,
                    process_id=str(data.get("process_id") or ""),
                    capabilities=list(data.get("capabilities") or []),
                    reason=str(data.get("reason") or ""),
                    status=str(data.get("status") or "pending"),
                    created_at_ts=float(data.get("created_at") or 0),
                    resolved_at=data.get("resolved_at"),
                    resolved_by=data.get("resolved_by"),
                ))
            else:
                existing.status = str(data.get("status") or existing.status)
                existing.capabilities = list(data.get("capabilities") or existing.capabilities or [])
                existing.reason = str(data.get("reason") or existing.reason or "")
                existing.resolved_at = data.get("resolved_at")
                existing.resolved_by = data.get("resolved_by")
            await session.commit()

    # ── 进程档案 ─────────────────────────────────────────────

    async def _upsert_process(self, data: dict[str, Any]) -> None:
        if self._session_factory is None:
            return
        from backend.models.agent_identity import KernelProcessRecord

        async with self._session_factory() as session:
            existing = (
                await session.execute(
                    select(KernelProcessRecord).where(
                        KernelProcessRecord.process_id == data["id"]
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                session.add(KernelProcessRecord(
                    process_id=data["id"],
                    identity_key=data.get("identity") or "main",
                    session_id=data.get("session_id"),
                    parent_process_id=data.get("parent_id"),
                    capabilities=data.get("capabilities"),
                    token_budget=data.get("token_budget"),
                    tokens_used=int(data.get("tokens_used") or 0),
                    state=str(data.get("state") or "created"),
                    started_at=data.get("started_at"),
                    ended_at=data.get("ended_at"),
                    exit_reason=data.get("exit_reason"),
                    meta=data.get("meta") or {},
                ))
            else:
                existing.state = str(data.get("state") or existing.state)
                existing.capabilities = data.get("capabilities")
                existing.tokens_used = int(data.get("tokens_used") or 0)
                existing.started_at = data.get("started_at")
                existing.ended_at = data.get("ended_at")
                existing.exit_reason = data.get("exit_reason")
            await session.commit()

    # ── checkpoint 快照 ──────────────────────────────────────

    async def write_checkpoint(self, *, tail_hash: str = "") -> Any:
        """写一次快照：身份 + 进程档案摘要 + 链尾哈希锚点。"""
        if self._session_factory is None:
            return None
        from backend.models.agent_identity import (
            AgentIdentity,
            KernelCheckpoint,
            KernelProcessRecord,
        )

        async with self._session_factory() as session:
            identities = (
                (await session.execute(select(AgentIdentity))).scalars().all()
            )
            alive_procs = (
                (await session.execute(
                    select(KernelProcessRecord).where(
                        KernelProcessRecord.state.in_(["created", "running"])
                    )
                )).scalars().all()
            )
            last_seq = (
                await session.execute(
                    select(KernelCheckpoint.seq).order_by(KernelCheckpoint.seq.desc()).limit(1)
                )
            ).scalar_one_or_none() or 0
            snapshot = {
                "identities": [
                    {
                        "id": str(i.id), "name": i.name, "role": i.role,
                        "status": i.status, "capabilities": i.capabilities,
                        "credit_score": i.credit_score,
                    }
                    for i in identities
                ],
                "processes": [
                    {"process_id": p.process_id, "state": p.state,
                     "identity_key": p.identity_key}
                    for p in alive_procs
                ],
                "ts": time.time(),
            }
            cp = KernelCheckpoint(
                seq=last_seq + 1,
                event_count=self._total_events,
                tail_hash=tail_hash,
                state_snapshot=snapshot,
            )
            session.add(cp)
            await session.commit()
            self._events_since_checkpoint = 0
            logger.info("kernel checkpoint #%d 落盘（%d 事件）", cp.seq, self._total_events)
            return cp

    async def latest_checkpoint(self) -> Any:
        if self._session_factory is None:
            return None
        from backend.models.agent_identity import KernelCheckpoint

        async with self._session_factory() as session:
            return (
                await session.execute(
                    select(KernelCheckpoint).order_by(KernelCheckpoint.seq.desc()).limit(1)
                )
            ).scalar_one_or_none()

    # ── 启动恢复 ─────────────────────────────────────────────

    async def project_rust_snapshots(self) -> dict[str, Any]:
        """P0.5 R3：从 Rust process_snapshot 投影到 DB KernelCheckpoint。

        权威在 Rust（tail_hash + process 态）；DB 仅作档案/观测投影。
        返回 {projected, tail_hash, plans}；host 不可用时空结果。
        """
        out: dict[str, Any] = {
            "projected": 0,
            "tail_hash": "",
            "plans": [],
            "source": "rust",
        }
        if self._session_factory is None:
            return out
        try:
            from backend.kernel import get_kernel

            k = get_kernel()
        except Exception:
            return out
        if not hasattr(k, "_call") and not hasattr(k, "process_recovery_plan"):
            return out

        # collect process ids from DB archive + live kernel
        pids: set[str] = set()
        try:
            from backend.models.agent_identity import KernelProcessRecord

            async with self._session_factory() as session:
                rows = (
                    await session.execute(select(KernelProcessRecord.process_id))
                ).scalars().all()
            for pid in rows:
                if pid:
                    pids.add(str(pid))
        except Exception as e:
            logger.debug("list process ids for rust project: %s", e)
        try:
            for p in k.list_processes(include_terminal=True) or []:
                pid = getattr(p, "id", None) or (p.get("id") if isinstance(p, dict) else None)
                if pid:
                    pids.add(str(pid))
        except Exception:
            pass

        best_tail = ""
        best_count = 0
        plans: list[dict[str, Any]] = []
        for pid in list(pids)[:200]:
            try:
                if hasattr(k, "process_recovery_plan"):
                    plan = k.process_recovery_plan(pid) or {}
                elif hasattr(k, "_acall"):
                    # audit-fix: async 上下文走 _acall，避免阻塞事件循环
                    plan = await k._acall("process_recovery_plan", {"process_id": pid}) or {}
                else:
                    plan = k._call("process_recovery_plan", {"process_id": pid}) or {}
                if not isinstance(plan, dict):
                    continue
                if plan.get("mode") != "snapshot_plus_incremental":
                    continue
                if plan.get("full_replay") is True:
                    continue
                plans.append({"process_id": pid, **{x: plan.get(x) for x in (
                    "snapshot_id", "seq", "tail_hash", "event_count", "mode"
                )}})
                th = str(plan.get("tail_hash") or "")
                ec = int(plan.get("event_count") or 0)
                if ec >= best_count and th:
                    best_count = ec
                    best_tail = th
            except Exception:
                continue
        out["plans"] = plans
        out["tail_hash"] = best_tail

        if best_tail or plans:
            try:
                snap_body = {
                    "source": "rust_process_snapshot",
                    "plans": plans[:50],
                    "ts": time.time(),
                }
                cp = await self.write_checkpoint(tail_hash=best_tail or "rust")
                # enrich last checkpoint meta via re-write path if needed
                if cp is not None and hasattr(cp, "state_snapshot"):
                    try:
                        existing = dict(cp.state_snapshot or {})
                        existing["rust_projection"] = snap_body
                        # best-effort: may be detached after commit; ignore
                    except Exception:
                        pass
                out["projected"] = len(plans)
                if cp is not None:
                    out["checkpoint_seq"] = getattr(cp, "seq", None)
            except Exception as e:
                logger.warning("project rust snapshot to DB failed: %s", e)
        return out

    async def recover(self) -> dict[str, Any]:
        """启动恢复。语义：

        1. created/running 进程档案 → interrupted（进程实际已死，诚实记录）
        2. **优先** Rust process_recovery_plan 投影到 DB（P0.5）
        3. 加载最新 checkpoint；恢复路径 = 快照 + tail_hash 后增量事件
        4. 增量事件只校验/计数，不 replay 进内存（进程不复活）
        返回恢复摘要（供观测与测试断言——含是否走了全量 replay）。
        """
        summary: dict[str, Any] = {
            "interrupted": 0,
            "checkpoint_seq": None,
            "incremental_events": 0,
            "full_replay": False,
            "escalations_hydrated": 0,
            "rust_projection": {},
        }
        if self._session_factory is None:
            return summary
        from backend.models.agent_identity import (
            KernelEscalationRecord,
            KernelProcessRecord,
        )

        async with self._session_factory() as session:
            result = await session.execute(
                update(KernelProcessRecord)
                .where(KernelProcessRecord.state.in_(["created", "running"]))
                .values(state="interrupted", ended_at=time.time(),
                        exit_reason="service restart")
            )
            summary["interrupted"] = result.rowcount or 0
            await session.commit()

        # 提权外部化：把 DB pending 注回本进程内存（多 worker 重启后仍可见待批）
        try:
            from backend.kernel.kernel import get_kernel

            kernel = get_kernel()
            async with self._session_factory() as session:
                rows = (
                    await session.execute(
                        select(KernelEscalationRecord).where(
                            KernelEscalationRecord.status == "pending"
                        )
                    )
                ).scalars().all()
            for row in rows:
                kernel.hydrate_escalation({
                    "id": row.escalation_id,
                    "process_id": row.process_id,
                    "capabilities": row.capabilities or [],
                    "reason": row.reason or "",
                    "status": row.status,
                    "created_at": row.created_at_ts or 0,
                    "resolved_at": row.resolved_at,
                    "resolved_by": row.resolved_by,
                })
            summary["escalations_hydrated"] = len(rows)
        except Exception as e:
            logger.warning("kernel escalation 恢复失败（不阻断）: %s", e)

        # P0.5：先从 Rust 投影进程快照到 DB，再读最新 checkpoint
        try:
            proj = await self.project_rust_snapshots()
            summary["rust_projection"] = proj
            if proj.get("projected"):
                summary["full_replay"] = False
                if proj.get("checkpoint_seq") is not None:
                    summary["checkpoint_seq"] = proj.get("checkpoint_seq")
        except Exception as e:
            logger.warning("rust snapshot project skip: %s", e)

        cp = await self.latest_checkpoint()
        if cp is not None:
            summary["checkpoint_seq"] = cp.seq
            self._total_events = cp.event_count
            if self._audit_store is not None:
                # 恢复路径红线：只读 tail_hash 之后的增量，禁止全量 replay
                delta = self._audit_store.read_after(cp.tail_hash or None)
                summary["incremental_events"] = len(delta)
                self._total_events += len(delta)
            summary["full_replay"] = False
        elif self._audit_store is not None:
            # 无快照的首次启动：从头读是合法的（此时还没有历史）
            delta = self._audit_store.read_after(None)
            summary["incremental_events"] = len(delta)
            self._total_events = len(delta)
            # 仅当确实有历史事件且无任何快照时才标 full_replay
            summary["full_replay"] = bool(delta)
        return summary
