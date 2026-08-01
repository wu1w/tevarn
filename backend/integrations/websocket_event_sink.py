"""EventSinkPort wrapping ws_manager + optional progress_sink.

Matches NexusAgentLoop._push_status / _push_stream broadcast shape:
  ws_manager.broadcast(session_id: UUID, message: dict)
"""
from __future__ import annotations

import logging
import uuid
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)


class WebSocketEventSink:
    def __init__(self, ws_manager: Any = None, progress_sink: Any = None) -> None:
        self.ws_manager = ws_manager
        self.progress_sink = progress_sink

    async def push_status(
        self,
        session_id: UUID,
        state: str,
        detail: str = "",
        *,
        caps_count: int | None = None,
        tools_count: int | None = None,
    ) -> None:
        """与 loop_io._push_status / StatusUpdate 字段集对齐（含 caps/tools）。"""
        if self.ws_manager:
            try:
                from backend.schemas.ws import StatusUpdate

                payload = StatusUpdate(
                    session_id=session_id,
                    state=state,
                    detail=detail or None,
                    caps_count=caps_count,
                    tools_count=tools_count,
                ).model_dump(mode="json")
                await self.ws_manager.broadcast(session_id, payload)
            except Exception as e:
                logger.debug("EventSink push_status skipped: %s", e)
        # 社交通道：仅 error 细节
        if self.progress_sink and state == "error" and detail:
            try:
                res = self.progress_sink("error", detail)
                if hasattr(res, "__await__"):
                    await res
            except Exception:
                pass

    async def push_stream_delta(
        self,
        session_id: UUID,
        delta: str,
        *,
        done: bool = False,
        message_id: UUID | None = None,
    ) -> None:
        if not self.ws_manager:
            return
        try:
            from backend.schemas.ws import StreamDelta

            mid = message_id or uuid.uuid4()
            payload = StreamDelta(
                session_id=session_id,
                message_id=mid,
                content=delta or "",
            ).model_dump(mode="json")
            # done 标记：schema 无字段时塞入扩展（前端可忽略）
            if done:
                payload["done"] = True
            await self.ws_manager.broadcast(session_id, payload)
        except Exception as e:
            logger.debug("EventSink push_stream_delta skipped: %s", e)
