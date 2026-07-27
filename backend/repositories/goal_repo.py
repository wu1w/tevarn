"""Goal Repository 接口与实现"""

import uuid
from typing import Any

from sqlalchemy import select, update

from backend.models.goal import Goal

from .base import AsyncBaseRepository, BaseRepository


class GoalRepository(BaseRepository):
    """Goal 仓库接口"""

    pass


class AsyncGoalRepository(AsyncBaseRepository, GoalRepository):
    """基于 SQLAlchemy async session 的 Goal 仓库实现"""

    async def get_by_id(self, id: Any) -> Goal | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(Goal).where(Goal.id == id))
            return result.scalar_one_or_none()
        finally:
            await self._close_session(session)

    async def create(self, data: dict[str, Any]) -> Goal:
        session = await self._get_session()
        try:
            goal = Goal(**data)
            session.add(goal)
            await self._maybe_commit(session)
            await session.refresh(goal)
            return goal
        finally:
            await self._close_session(session)

    async def update(self, id: Any, data: dict[str, Any]) -> Goal | None:
        session = await self._get_session()
        try:
            stmt = update(Goal).where(Goal.id == id).values(**data).returning(Goal)
            result = await session.execute(stmt)
            await self._maybe_commit(session)
            return result.scalar_one_or_none()
        finally:
            await self._close_session(session)

    async def delete(self, id: Any) -> bool:
        session = await self._get_session()
        try:
            goal = await session.get(Goal, id)
            if goal is None:
                return False
            await session.delete(goal)
            await self._maybe_commit(session)
            return True
        finally:
            await self._close_session(session)

    async def list_objectives(self) -> list[Goal]:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(Goal).where(Goal.kind == "objective").order_by(Goal.created_at.desc())
            )
            return list(result.scalars().all())
        finally:
            await self._close_session(session)

    async def list_all(self) -> list[Goal]:
        session = await self._get_session()
        try:
            result = await session.execute(select(Goal).order_by(Goal.created_at.desc()))
            return list(result.scalars().all())
        finally:
            await self._close_session(session)

    async def list_children(self, parent_id: uuid.UUID) -> list[Goal]:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(Goal).where(Goal.parent_id == parent_id).order_by(Goal.created_at.asc())
            )
            return list(result.scalars().all())
        finally:
            await self._close_session(session)
