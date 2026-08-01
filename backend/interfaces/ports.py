"""Agent core ports (Batch 3a)."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable
from uuid import UUID


@runtime_checkable
class MessageStorePort(Protocol):
    """Persist and load chat messages (SQLAlchemy today; other backends later)."""

    async def save_message(
        self,
        session_id: UUID,
        role: str,
        content: str,
        tool_calls: list[dict[str, Any]] | None = None,
        token_count: int | None = None,
    ) -> Any: ...

    async def get_history(
        self,
        session_id: UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Any]: ...


@runtime_checkable
class EventSinkPort(Protocol):
    """Push status / stream / tool events to UI or channels.

    push_status 字段集须与 StatusUpdate / loop_io._push_status 一致
   （含 caps_count / tools_count，避免 sink 短路丢可观测字段）。
    """

    async def push_status(
        self,
        session_id: UUID,
        state: str,
        detail: str = "",
        *,
        caps_count: int | None = None,
        tools_count: int | None = None,
    ) -> None: ...

    async def push_stream_delta(
        self, session_id: UUID, delta: str, *, done: bool = False
    ) -> None: ...


@runtime_checkable
class ToolExecutorPort(Protocol):
    """Execute a named tool with arguments; return string/JSON result."""

    async def execute(self, name: str, arguments: dict[str, Any]) -> Any: ...

    def list_schemas(self, names: set[str] | None = None) -> list[dict[str, Any]]: ...


@runtime_checkable
class LLMPort(Protocol):
    """Minimal LLM surface used by agent loops (factory adapters wrap concrete clients)."""

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> Any: ...
