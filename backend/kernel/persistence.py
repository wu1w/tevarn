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
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._events_since_checkpoint = 0
        self._total_events = 0

    def sink(self) -> Callable[[dict[str, Any]], None]:
        """kernel 同步侧挂载点（put_nowait 是同步方法，零 await）。"""
        return self._queue.put_nowait

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
            elif kind == "event":
                self._total_events += 1
                self._events_since_checkpoint += 1
                if self._events_since_checkpoint >= self._checkpoint_interval:
                    await self.write_checkpoint(tail_hash=str(op.get("data", {}).get("hash") or ""))
        except Exception as e:
            logger.warning("kernel 持久化失败（不阻断）op=%s: %s", op.get("op"), e)

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

    async def recover(self) -> dict[str, Any]:
        """启动恢复。语义：

        1. created/running 进程档案 → interrupted（进程实际已死，诚实记录）
        2. 加载最新 checkpoint；恢复路径 = 快照 + tail_hash 后增量事件
        3. 增量事件只校验/计数，不 replay 进内存（进程不复活；
           身份记忆消费层 0.6 接管增量）
        返回恢复摘要（供观测与测试断言——含是否走了全量 replay）。
        """
        summary: dict[str, Any] = {
            "interrupted": 0,
            "checkpoint_seq": None,
            "incremental_events": 0,
            "full_replay": False,
        }
        if self._session_factory is None:
            return summary
        from backend.models.agent_identity import KernelProcessRecord

        async with self._session_factory() as session:
            result = await session.execute(
                update(KernelProcessRecord)
                .where(KernelProcessRecord.state.in_(["created", "running"]))
                .values(state="interrupted", ended_at=time.time(),
                        exit_reason="service restart")
            )
            summary["interrupted"] = result.rowcount or 0
            await session.commit()

        cp = await self.latest_checkpoint()
        if cp is not None:
            summary["checkpoint_seq"] = cp.seq
            self._total_events = cp.event_count
            if self._audit_store is not None:
                # 恢复路径红线：只读 tail_hash 之后的增量，禁止全量 replay
                delta = self._audit_store.read_after(cp.tail_hash or None)
                summary["incremental_events"] = len(delta)
                self._total_events += len(delta)
        elif self._audit_store is not None:
            # 无快照的首次启动：从头读是合法的（此时还没有历史）
            delta = self._audit_store.read_after(None)
            summary["incremental_events"] = len(delta)
            self._total_events = len(delta)
            summary["full_replay"] = bool(delta)
        return summary
