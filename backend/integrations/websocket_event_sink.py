"""EventSinkPort wrapping ws_manager + optional progress_sink."""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)


class WebSocketEventSink:
    def __init__(self, ws_manager: Any = None, progress_sink: Any = None) -> None:
        self.ws_manager = ws_manager
        self.progress_sink = progress_sink

    async def push_status(self, session_id: UUID, state: str, detail: str = "") -> None:
        if not self.ws_manager:
            return
        try:
            from backend.schemas.ws import StatusUpdate

            msg = StatusUpdate(session_id=str(session_id), state=state, detail=detail or "")
            await self.ws_manager.broadcast(str(session_id), msg)
        except Exception as e:
            logger.debug("EventSink push_status skipped: %s", e)
        if self.progress_sink and detail:
            try:
                res = self.progress_sink("status", detail)
                if hasattr(res, "__await__"):
                    await res
            except Exception:
                pass

    async def push_stream_delta(
        self, session_id: UUID, delta: str, *, done: bool = False
    ) -> None:
        if not self.ws_manager:
            return
        try:
            from backend.schemas.ws import StreamDelta

            msg = StreamDelta(session_id=str(session_id), delta=delta, done=done)
            await self.ws_manager.broadcast(str(session_id), msg)
        except Exception as e:
            logger.debug("EventSink push_stream_delta skipped: %s", e)
