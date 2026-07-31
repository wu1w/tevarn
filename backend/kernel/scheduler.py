"""多 Agent 调度器雏形。

.. deprecated:: P0-A / R3 去双轨
    **生产权威**：Rust ``scheduler_*`` / ``run_gate_*`` RPC。
    本文件仅 fallback / 历史测试；禁止扩展生产队列逻辑。

H-07：Session 锁只保证同会话不重入，不是全局调度器；
跨会话排队与前台优先由 run_gate + priority_class 完成。
"""

from __future__ import annotations

import heapq
import itertools
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

_AGE_THRESHOLD_SECONDS = 30.0  # 等待超过 30s 提一档
_AGE_BOOST = 1  # 每次提升的优先级档数


@dataclass(order=True)
class ScheduledTask:
    """优先级队列元素。heapq 按 (effective_priority, seq) 排序。"""

    effective_priority: int
    seq: int  # 同优先级 FIFO 序号
    id: str = field(compare=False, default_factory=lambda: uuid.uuid4().hex[:12])
    process_id: str = field(compare=False, default="")
    payload: dict[str, Any] = field(compare=False, default_factory=dict)
    base_priority: int = field(compare=False, default=10)
    submitted_at: float = field(compare=False, default_factory=time.time)
    state: str = field(compare=False, default="queued")  # queued/running/done/cancelled


class AgentScheduler:
    """优先级调度器（雏形）。

    线程安全由调用方保证（asyncio 单线程语义下无需锁）。
    """

    def __init__(self, *, age_threshold: float = _AGE_THRESHOLD_SECONDS) -> None:
        self._heap: list[ScheduledTask] = []
        self._tasks: dict[str, ScheduledTask] = {}
        self._seq = itertools.count()
        self._age_threshold = age_threshold

    def submit(
        self,
        process_id: str,
        payload: dict[str, Any] | None = None,
        *,
        priority: int = 10,
    ) -> ScheduledTask:
        task = ScheduledTask(
            effective_priority=max(0, priority),
            seq=next(self._seq),
            process_id=process_id,
            payload=dict(payload or {}),
            base_priority=max(0, priority),
        )
        self._tasks[task.id] = task
        heapq.heappush(self._heap, task)
        return task

    def _apply_aging(self) -> None:
        """等待过久的任务提升优先级（重建堆）。O(n)，任务量小可接受。"""
        now = time.time()
        dirty = False
        for t in self._tasks.values():
            if t.state != "queued":
                continue
            waited = now - t.submitted_at
            boost = int(waited // self._age_threshold) * _AGE_BOOST
            new_prio = max(0, t.base_priority - boost)
            if new_prio != t.effective_priority:
                t.effective_priority = new_prio
                dirty = True
        if dirty:
            heapq.heapify(self._heap)

    def next(self) -> ScheduledTask | None:
        """取出下一个应执行的任务（标记 running）。"""
        self._apply_aging()
        while self._heap:
            task = heapq.heappop(self._heap)
            if task.id in self._tasks and task.state == "queued":
                task.state = "running"
                return task
        return None

    def complete(self, task_id: str, *, cancelled: bool = False) -> None:
        task = self._tasks.get(task_id)
        if task is not None:
            task.state = "cancelled" if cancelled else "done"
            # 终态记录有界保留（供 stats/审计），超上限清最旧
            terminal = [t for t in self._tasks.values() if t.state in ("done", "cancelled")]
            if len(terminal) > 1000:
                for t in sorted(terminal, key=lambda x: x.seq)[: len(terminal) - 1000]:
                    self._tasks.pop(t.id, None)

    def cancel_process(self, process_id: str) -> int:
        """进程终止时取消其所有排队任务。返回取消数。"""
        n = 0
        for t in self._tasks.values():
            if t.process_id == process_id and t.state == "queued":
                t.state = "cancelled"
                n += 1
        return n

    def queued(self) -> list[ScheduledTask]:
        self._apply_aging()
        return sorted(
            (t for t in self._tasks.values() if t.state == "queued"),
            key=lambda t: (t.effective_priority, t.seq),
        )

    def stats(self) -> dict[str, int]:
        out: dict[str, int] = {"queued": 0, "running": 0, "done": 0, "cancelled": 0}
        for t in self._tasks.values():
            out[t.state] = out.get(t.state, 0) + 1
        return out
