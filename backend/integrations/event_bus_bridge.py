"""EventBus → WebSocket via unified run_events.emit_run_event.

All bus topics (run.* / tool.* / approval.* / computer.*) are forwarded with the
same wire format as direct emit_run_event calls:

  { type, event, topic, seq, session_id, timestamp, ts, data, payload, run_id? }

This collapses the dual-protocol split (legacy topic/data-only vs seq/event).
"""
from __future__ import annotations

import logging
import uuid
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
        sid_raw = (payload or {}).get("session_id")
        if not sid_raw:
            return
        try:
            sid = uuid.UUID(str(sid_raw))
        except (ValueError, AttributeError):
            return
        try:
            from backend.agent.run_events import emit_run_event

            run_id = (payload or {}).get("run_id")
            gen = (payload or {}).get("generation") or (payload or {}).get(
                "run_generation"
            )
            detail = None
            if isinstance(payload, dict):
                detail = payload.get("detail") or payload.get("note") or payload.get(
                    "message"
                )
                if detail is not None:
                    detail = str(detail)[:500]
            await emit_run_event(
                self.ws_manager,
                sid,
                topic,  # event == topic
                detail=detail,
                run_id=str(run_id) if run_id else None,
                payload=dict(payload or {}),
                data=dict(payload or {}),
                generation=int(gen) if gen is not None else None,
            )
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
