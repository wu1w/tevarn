"""
CronHook Repository 接口与实现
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.cron_hook import CronHook, CronHookExecutionLog
from backend.repositories.base import AsyncBaseRepository, BaseRepository


class CronHookRepository(BaseRepository):
    """CronHook 仓库接口"""

    async def list_by_cron_job(self, cron_job_id: uuid.UUID) -> list[CronHook]:
        raise NotImplementedError


class AsyncCronHookRepository(AsyncBaseRepository, CronHookRepository):
    """CronHook 异步仓库实现"""

    async def get_by_id(self, id: Any) -> CronHook | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(CronHook).where(CronHook.id == id))
            return result.scalar_one_or_none()
        finally:
            await self._close_session(session)

    async def create(self, data: dict[str, Any]) -> CronHook:
        session = await self._get_session()
        try:
            obj = CronHook(**data)
            session.add(obj)
            await self._maybe_commit(session)
            await session.refresh(obj)
            return obj
        finally:
            await self._close_session(session)

    async def update(self, id: Any, data: dict[str, Any]) -> CronHook | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(CronHook).where(CronHook.id == id))
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
            result = await session.execute(select(CronHook).where(CronHook.id == id))
            obj = result.scalar_one_or_none()
            if not obj:
                return False
            await session.delete(obj)
            await self._maybe_commit(session)
            return True
        finally:
            await self._close_session(session)

    async def list_by_cron_job(self, cron_job_id: uuid.UUID) -> list[CronHook]:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(CronHook)
                .where(CronHook.cron_job_id == cron_job_id)
                .order_by(CronHook.name)
            )
            return list(result.scalars().all())
        finally:
            await self._close_session(session)

    async def list_by_user(self, user_id: uuid.UUID) -> list[CronHook]:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(CronHook)
                .where(CronHook.user_id == user_id)
                .order_by(CronHook.name)
            )
            return list(result.scalars().all())
        finally:
            await self._close_session(session)


class AsyncCronHookExecutionLogRepository(AsyncBaseRepository):
    """CronHook 执行日志仓库"""

    async def get_by_id(self, id: Any) -> CronHookExecutionLog | None:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(CronHookExecutionLog).where(CronHookExecutionLog.id == id)
            )
            return result.scalar_one_or_none()
        finally:
            await self._close_session(session)

    async def create(self, data: dict[str, Any]) -> CronHookExecutionLog:
        session = await self._get_session()
        try:
            obj = CronHookExecutionLog(**data)
            session.add(obj)
            await self._maybe_commit(session)
            await session.refresh(obj)
            return obj
        finally:
            await self._close_session(session)

    async def update(self, id: Any, data: dict[str, Any]) -> CronHookExecutionLog | None:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(CronHookExecutionLog).where(CronHookExecutionLog.id == id)
            )
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
        return True  # 日志不单独删除

    async def list_by_hook(
        self, hook_id: uuid.UUID, limit: int = 50
    ) -> list[CronHookExecutionLog]:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(CronHookExecutionLog)
                .where(CronHookExecutionLog.hook_id == hook_id)
                .order_by(CronHookExecutionLog.created_at.desc())
                .limit(limit)
            )
            return list(result.scalars().all())
        finally:
            await self._close_session(session)
