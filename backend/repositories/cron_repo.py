"""
Cron Repository 接口与实现
"""

import uuid
from abc import abstractmethod
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_, select

from backend.models.cron import CronJob

from .base import AsyncBaseRepository, BaseRepository


class CronJobRepository(BaseRepository):
    """CronJob 仓库接口"""

    @abstractmethod
    async def list_all(self) -> list[Any]:
        """列出所有定时任务"""
        raise NotImplementedError

    @abstractmethod
    async def list_enabled(self) -> list[Any]:
        """列出所有启用的定时任务"""
        raise NotImplementedError

    @abstractmethod
    async def list_by_user(self, user_id: uuid.UUID) -> list[Any]:
        """列出当前用户可见的定时任务（含全局共享）"""
        raise NotImplementedError

    @abstractmethod
    async def update_run_status(
        self,
        cron_id: uuid.UUID,
        status: str,
        error: str | None = None,
    ) -> Any | None:
        """更新任务运行状态"""
        raise NotImplementedError


class AsyncCronJobRepository(AsyncBaseRepository, CronJobRepository):
    """基于 SQLAlchemy async session 的 CronJob 仓库实现"""


    async def get_by_id(self, id: Any) -> CronJob | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(CronJob).where(CronJob.id == id))
            return result.scalar_one_or_none()
        finally:
            await self._close_session(session)

    async def create(self, data: dict[str, Any]) -> CronJob:
        session = await self._get_session()
        try:
            job = CronJob(**data)
            session.add(job)
            await self._maybe_commit(session)
            await session.refresh(job)
            return job
        finally:
            await self._close_session(session)

    async def update(self, id: Any, data: dict[str, Any]) -> CronJob | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(CronJob).where(CronJob.id == id))
            job = result.scalar_one_or_none()
            if not job:
                return None
            for key, value in data.items():
                setattr(job, key, value)
            await self._maybe_commit(session)
            await session.refresh(job)
            return job
        finally:
            await self._close_session(session)

    async def delete(self, id: Any) -> bool:
        session = await self._get_session()
        try:
            result = await session.execute(select(CronJob).where(CronJob.id == id))
            job = result.scalar_one_or_none()
            if not job:
                return False
            await session.delete(job)
            await self._maybe_commit(session)
            return True
        finally:
            await self._close_session(session)

    async def list_all(self) -> list[CronJob]:
        session = await self._get_session()
        try:
            result = await session.execute(select(CronJob).order_by(CronJob.name))
            return list(result.scalars().all())
        finally:
            await self._close_session(session)

    async def list_by_user(self, user_id: uuid.UUID) -> list[CronJob]:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(CronJob)
                .where(
                    or_(
                        CronJob.user_id == user_id,
                        CronJob.user_id.is_(None),
                    )
                )
                .order_by(CronJob.name)
            )
            return list(result.scalars().all())
        finally:
            await self._close_session(session)

    async def list_enabled(self) -> list[CronJob]:
        session = await self._get_session()
        try:
            result = await session.execute(select(CronJob).where(CronJob.enabled.is_(True)))
            return list(result.scalars().all())
        finally:
            await self._close_session(session)

    async def update_run_status(
        self,
        cron_id: uuid.UUID,
        status: str,
        error: str | None = None,
        *,
        next_run_at: datetime | None = None,
    ) -> CronJob | None:
        data: dict[str, Any] = {
            "last_status": status,
            "last_run_at": datetime.now(timezone.utc),
        }
        if error is not None:
            data["last_error"] = error
        elif status == "success":
            data["last_error"] = None
        if next_run_at is not None:
            data["next_run_at"] = next_run_at
        return await self.update(cron_id, data)
