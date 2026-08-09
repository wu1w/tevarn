"""Thin AgentLoopBase — shared stop flag + optional ports (Batch 3a).

Production path remains ``NexusAgentLoop`` which subclasses this base.
Does NOT reimplement the tool loop; only structural unification.
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from backend.interfaces.ports import EventSinkPort, MessageStorePort, ToolExecutorPort

logger = logging.getLogger(__name__)


class AgentLoopBase:
    """Common lifecycle helpers + port holders for agent loops."""

    def __init__(
        self,
        *,
        message_store: MessageStorePort | None = None,
        event_sink: EventSinkPort | None = None,
        tool_executor: ToolExecutorPort | None = None,
        agent_name: str = "Tevarn",
        user_id: UUID | None = None,
    ) -> None:
        self.message_store = message_store
        self.event_sink = event_sink
        self.tool_executor = tool_executor
        self.agent_name = agent_name
        self.user_id = user_id
        self._should_stop = False

    def stop(self) -> None:
        self._should_stop = True
        logger.info("Stop signal set for agent loop (%s)", type(self).__name__)

    def reset_stop(self) -> None:
        self._should_stop = False

    @property
    def should_stop(self) -> bool:
        return bool(self._should_stop)

    async def store_save_message(
        self,
        session_id: UUID,
        role: str,
        content: str,
        tool_calls: list[dict[str, Any]] | None = None,
        token_count: int | None = None,
    ) -> Any:
        """Prefer MessageStorePort when injected; else subclasses use message_repo."""
        if self.message_store is not None:
            return await self.message_store.save_message(
                session_id, role, content, tool_calls=tool_calls, token_count=token_count
            )
        raise RuntimeError("no message_store; subclass must override persist")

    async def store_get_history(
        self, session_id: UUID, limit: int = 100, offset: int = 0
    ) -> list[Any]:
        if self.message_store is not None:
            return await self.message_store.get_history(session_id, limit=limit, offset=offset)
        raise RuntimeError("no message_store; subclass must override history load")
