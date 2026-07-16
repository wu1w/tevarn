"""
SubAgent Repository 接口与实现
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.sub_agent import SubAgent
from backend.repositories.base import AsyncBaseRepository, BaseRepository


class SubAgentRepository(BaseRepository):
    """SubAgent 仓库接口"""

    async def list_by_user(self, user_id: uuid.UUID) -> list[SubAgent]:
        raise NotImplementedError

    async def list_enabled(self) -> list[SubAgent]:
        raise NotImplementedError

    async def list_builtins(self) -> list[SubAgent]:
        raise NotImplementedError


class AsyncSubAgentRepository(AsyncBaseRepository, SubAgentRepository):
    """SubAgent 异步仓库实现"""

    async def get_by_id(self, id: Any) -> SubAgent | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(SubAgent).where(SubAgent.id == id))
            return result.scalar_one_or_none()
        finally:
            await self._close_session(session)

    async def create(self, data: dict[str, Any]) -> SubAgent:
        session = await self._get_session()
        try:
            obj = SubAgent(**data)
            session.add(obj)
            await self._maybe_commit(session)
            await session.refresh(obj)
            return obj
        finally:
            await self._close_session(session)

    async def update(self, id: Any, data: dict[str, Any]) -> SubAgent | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(SubAgent).where(SubAgent.id == id))
            obj = result.scalar_one_or_none()
            if not obj:
                return None
            for key, value in data.items():
                setattr(obj, key, value)
            await self._maybe_commit(session)
            await session.refresh(obj)
            return obj
        finally:
            await self._close_session(session)

    async def delete(self, id: Any) -> bool:
        session = await self._get_session()
        try:
            result = await session.execute(select(SubAgent).where(SubAgent.id == id))
            obj = result.scalar_one_or_none()
            if not obj:
                return False
            await session.delete(obj)
            await self._maybe_commit(session)
            return True
        finally:
            await self._close_session(session)

    async def list_by_user(self, user_id: uuid.UUID) -> list[SubAgent]:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(SubAgent)
                .where(SubAgent.user_id == user_id)
                .order_by(SubAgent.sort_order, SubAgent.name)
            )
            return list(result.scalars().all())
        finally:
            await self._close_session(session)

    async def list_all(self) -> list[SubAgent]:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(SubAgent).order_by(SubAgent.sort_order, SubAgent.name)
            )
            return list(result.scalars().all())
        finally:
            await self._close_session(session)

    async def list_enabled(self) -> list[SubAgent]:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(SubAgent)
                .where(SubAgent.enabled.is_(True))
                .order_by(SubAgent.sort_order, SubAgent.name)
            )
            return list(result.scalars().all())
        finally:
            await self._close_session(session)

    async def list_builtins(self) -> list[SubAgent]:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(SubAgent)
                .where(SubAgent.is_builtin.is_(True))
                .order_by(SubAgent.sort_order, SubAgent.name)
            )
            return list(result.scalars().all())
        finally:
            await self._close_session(session)
