"""MessageStorePort over SQLAlchemy MessageRepository (no dual schema)."""
from __future__ import annotations

from typing import Any
from uuid import UUID


class SqlAlchemyMessageStore:
    """Thin adapter: delegates to existing MessageRepository / AsyncMessageRepository."""

    def __init__(self, message_repo: Any) -> None:
        if message_repo is None:
            raise ValueError("message_repo required")
        self._repo = message_repo

    async def save_message(
        self,
        session_id: UUID,
        role: str,
        content: str,
        tool_calls: list[dict[str, Any]] | None = None,
        token_count: int | None = None,
    ) -> Any:
        return await self._repo.save_message(
            session_id,
            role,
            content,
            tool_calls=tool_calls,
            token_count=token_count,
        )

    async def get_history(
        self,
        session_id: UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Any]:
        # repo method name differs slightly
        if hasattr(self._repo, "get_history_by_session"):
            return await self._repo.get_history_by_session(
                session_id, limit=limit, offset=offset
            )
        if hasattr(self._repo, "get_history"):
            return await self._repo.get_history(session_id, limit=limit, offset=offset)
        raise AttributeError("message_repo has no history method")
