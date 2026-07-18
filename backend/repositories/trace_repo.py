"""Session Trace 仓库 — 透明化轨迹存取"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.trace import SessionTrace


class TraceRepository:
    """轨迹记录的异步存取"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, data: dict[str, Any]) -> SessionTrace:
        trace = SessionTrace(**data)
        self.db.add(trace)
        await self.db.flush()
        return trace

    async def get_by_id(self, trace_id: uuid.UUID) -> SessionTrace | None:
        result = await self.db.execute(
            select(SessionTrace).where(SessionTrace.id == trace_id)
        )
        return result.scalar_one_or_none()

    async def list_by_session(
        self,
        session_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SessionTrace]:
        result = await self.db.execute(
            select(SessionTrace)
            .where(SessionTrace.session_id == session_id)
            .order_by(desc(SessionTrace.created_at))
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def get_latest_by_session(
        self, session_id: uuid.UUID
    ) -> SessionTrace | None:
        result = await self.db.execute(
            select(SessionTrace)
            .where(SessionTrace.session_id == session_id)
            .order_by(desc(SessionTrace.created_at))
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_by_user(
        self,
        user_id: uuid.UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SessionTrace]:
        result = await self.db.execute(
            select(SessionTrace)
            .where(SessionTrace.user_id == user_id)
            .order_by(desc(SessionTrace.created_at))
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def delete(self, trace_id: uuid.UUID) -> bool:
        trace = await self.get_by_id(trace_id)
        if not trace:
            return False
        await self.db.delete(trace)
        return True
