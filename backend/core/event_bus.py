"""进程内异步 EventBus（Phase 0.5.2）

命名空间约定：run.* / step.* / tool.* / approval.* / computer.*
- 订阅用 fnmatch 模式（"run.*"、"*"）
- publish 失败静默（单个订阅者异常不影响其他订阅者与主流程）
- 进程内总线，不持久化；跨进程/持久事件流后续再叠加

用法：
    from backend.core.event_bus import event_bus
    unsub = event_bus.subscribe("run.*", handler)   # handler(topic, payload) 协程
    await event_bus.publish("run.created", {"run_id": "...", ...})
"""
from __future__ import annotations

import asyncio
import fnmatch
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

EventHandler = Callable[[str, dict[str, Any]], Awaitable[None]]


class EventBus:
    """轻量 async pub/sub；订阅者按注册顺序逐个 await（保持事件顺序）"""

    def __init__(self) -> None:
        self._subs: list[tuple[str, EventHandler]] = []
        self._lock = asyncio.Lock()

    def subscribe(self, pattern: str, handler: EventHandler) -> Callable[[], None]:
        """注册订阅，返回取消函数"""
        entry = (pattern, handler)
        self._subs.append(entry)

        def _unsub() -> None:
            try:
                self._subs.remove(entry)
            except ValueError:
                pass

        return _unsub

    async def publish(self, topic: str, payload: dict[str, Any]) -> None:
        if not self._subs:
            return
        # 复制快照，避免订阅者在回调中增删导致迭代异常
        for pattern, handler in list(self._subs):
            if not fnmatch.fnmatchcase(topic, pattern):
                continue
            try:
                await handler(topic, payload)
            except Exception as e:
                logger.warning("event_bus subscriber failed topic=%s: %s", topic, e)

    def subscriber_count(self, pattern: str | None = None) -> int:
        if pattern is None:
            return len(self._subs)
        return sum(1 for p, _ in self._subs if p == pattern)


# 全局单例：loop / recorder / API / WS 桥共用一个总线
event_bus = EventBus()
