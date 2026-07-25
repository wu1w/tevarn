"""EventBus → WebSocket 活动流桥（Phase 0.5.2 W2-3）

订阅进程内 event_bus 的 run.* / tool.* / approval.* 事件，
按 payload.session_id 转发到对应会话的 WS 连接，消息类型 run_event。
前端活动流订阅 run_event 即可展示运行生命周期（状态迁移 / 工具步骤 / 确认请求）。

- 无 session_id 的事件不转发（纯内部事件）
- 连接不存在时 broadcast 静默忽略（ConnectionManager 既有行为）
- start/stop 幂等；转发异常只记 debug，不影响总线其他订阅者
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)

BRIDGE_PATTERNS = ("run.*", "tool.*", "approval.*", "computer.*")


class EventBusWSBridge:
    def __init__(self, ws_manager: Any) -> None:
        self.ws_manager = ws_manager
        self._unsubs: list[Callable[[], None]] = []

    def start(self) -> None:
        if self._unsubs:
            return
        from backend.core.event_bus import event_bus

        for pattern in BRIDGE_PATTERNS:
            self._unsubs.append(event_bus.subscribe(pattern, self._forward))
        logger.info("event_bus → WS bridge started patterns=%s", BRIDGE_PATTERNS)

    def stop(self) -> None:
        for unsub in self._unsubs:
            try:
                unsub()
            except Exception:
                pass
        self._unsubs = []

    @property
    def running(self) -> bool:
        return bool(self._unsubs)

    async def _forward(self, topic: str, payload: dict[str, Any]) -> None:
        if self.ws_manager is None:
            return
        sid_raw = payload.get("session_id")
        if not sid_raw:
            return
        try:
            sid = uuid.UUID(str(sid_raw))
        except (ValueError, AttributeError):
            return
        msg = {
            "type": "run_event",
            "topic": topic,
            "session_id": str(sid),
            "ts": datetime.now(timezone.utc).isoformat(),
            "data": payload,
        }
        try:
            await self.ws_manager.broadcast(sid, msg)
        except Exception as e:
            logger.debug("run_event forward failed topic=%s: %s", topic, e)


# main.py lifespan 持有；测试可自建实例
bridge: EventBusWSBridge | None = None


def start_bridge(ws_manager: Any) -> EventBusWSBridge:
    """启动全局桥（幂等）"""
    global bridge
    if bridge is None:
        bridge = EventBusWSBridge(ws_manager)
    bridge.start()
    return bridge


def stop_bridge() -> None:
    global bridge
    if bridge is not None:
        bridge.stop()
        bridge = None
