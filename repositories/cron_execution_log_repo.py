"""
Cron 执行日志仓库
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from backend.models.cron_execution_log import CronExecutionLog

from .base import AsyncBaseRepository


class AsyncCronExecutionLogRepository(AsyncBaseRepository):
    """基于 SQLAlchemy async session 的 Cron 执行日志仓库"""

    async def get_by_id(self, id: Any) -> CronExecutionLog | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(CronExecutionLog).where(CronExecutionLog.id == id))
            return result.scalar_one_or_none()
        finally:
            await self._close_session(session)

    async def create(self, data: dict[str, Any]) -> CronExecutionLog:
        session = await self._get_session()
        try:
            log = CronExecutionLog(**data)
            session.add(log)
            await self._maybe_commit(session)
            await session.refresh(log)
            return log
        finally:
            await self._close_session(session)

    async def list_by_cron_job(self, cron_job_id: uuid.UUID, limit: int = 50) -> list[CronExecutionLog]:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(CronExecutionLog)
                .where(CronExecutionLog.cron_job_id == cron_job_id)
                .order_by(CronExecutionLog.created_at.desc())
                .limit(limit)
            )
            return list(result.scalars().all())
        finally:
            await self._close_session(session)

    async def finish(
        self,
        log_id: uuid.UUID,
        status: str,
        output: str | None = None,
        error: str | None = None,
    ) -> CronExecutionLog | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(CronExecutionLog).where(CronExecutionLog.id == log_id))
            log = result.scalar_one_or_none()
            if not log:
                return None
            log.status = status
            log.finished_at = datetime.now(timezone.utc)
            if output is not None:
                log.output = output
            if error is not None:
                log.error = error
            if log.started_at and log.finished_at:
                log.duration_ms = int((log.finished_at - log.started_at).total_seconds() * 1000)
            await self._maybe_commit(session)
            await session.refresh(log)
            return log
        finally:
            await self._close_session(session)
