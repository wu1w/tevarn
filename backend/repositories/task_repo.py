"""
Task Repository 接口与实现
处理异步任务的进度追踪和日志管理
"""

import uuid
from abc import abstractmethod
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, select

from backend.models.task import Task, TaskStatus
from backend.schemas.task import TaskRead

from .base import AsyncBaseRepository, BaseRepository


class TaskRepository(BaseRepository):
    """Task 仓库接口"""

    @abstractmethod
    async def create_task(
        self,
        session_id: uuid.UUID,
        name: str,
        description: str | None = None,
    ) -> Any:
        """创建新任务"""
        raise NotImplementedError

    @abstractmethod
    async def update_progress(
        self,
        task_id: uuid.UUID,
        progress: int,
        status: str | None = None,
    ) -> Any | None:
        """更新任务进度 (0-100) 和状态"""
        raise NotImplementedError

    @abstractmethod
    async def append_log(
        self,
        task_id: uuid.UUID,
        log_entry: dict[str, Any],
    ) -> Any | None:
        """追加任务日志"""
        raise NotImplementedError

    @abstractmethod
    async def get_active_tasks_by_session(
        self,
        session_id: uuid.UUID,
    ) -> list[Any]:
        """获取会话的所有活跃任务 (pending / running)"""
        raise NotImplementedError

    @abstractmethod
    async def get_tasks_by_session(
        self,
        session_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Any]:
        """分页获取会话的所有任务"""
        raise NotImplementedError


class AsyncTaskRepository(AsyncBaseRepository, TaskRepository):
    """基于 SQLAlchemy async session 的 Task 仓库实现"""


    async def get_by_id(self, id: Any) -> TaskRead | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(Task).where(Task.id == id))
            task = result.scalar_one_or_none()
            return TaskRead.model_validate(task) if task is not None else None
        finally:
            await self._close_session(session)

    async def create(self, data: dict[str, Any]) -> TaskRead:
        session = await self._get_session()
        try:
            task = Task(**data)
            session.add(task)
            await self._maybe_commit(session)
            await session.refresh(task)
            return TaskRead.model_validate(task)
        finally:
            await self._close_session(session)

    async def update(self, id: Any, data: dict[str, Any]) -> TaskRead | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(Task).where(Task.id == id))
            task = result.scalar_one_or_none()
            if not task:
                return None
            for key, value in data.items():
                setattr(task, key, value)
            await self._maybe_commit(session)
            await session.refresh(task)
            return TaskRead.model_validate(task)
        finally:
            await self._close_session(session)

    async def delete(self, id: Any) -> bool:
        session = await self._get_session()
        try:
            result = await session.execute(select(Task).where(Task.id == id))
            task = result.scalar_one_or_none()
            if not task:
                return False
            await session.delete(task)
            await self._maybe_commit(session)
            return True
        finally:
            await self._close_session(session)

    async def create_task(
        self,
        session_id: uuid.UUID,
        name: str,
        description: str | None = None,
    ) -> TaskRead:
        return await self.create(
            {
                "session_id": session_id,
                "name": name,
                "description": description,
                "status": TaskStatus.PENDING,
                "progress": 0,
                "logs": [],
            }
        )

    async def update_progress(
        self,
        task_id: uuid.UUID,
        progress: int,
        status: str | None = None,
    ) -> TaskRead | None:
        data: dict[str, Any] = {"progress": progress}
        if status is not None:
            data["status"] = status
        return await self.update(task_id, data)

    async def append_log(
        self,
        task_id: uuid.UUID,
        log_entry: dict[str, Any],
    ) -> TaskRead | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(Task).where(Task.id == task_id))
            task = result.scalar_one_or_none()
            if not task:
                return None
            logs = list(task.logs) if task.logs else []
            log_entry["timestamp"] = datetime.now(timezone.utc).isoformat()
            logs.append(log_entry)
            task.logs = logs
            await self._maybe_commit(session)
            await session.refresh(task)
            return TaskRead.model_validate(task)
        finally:
            await self._close_session(session)

    async def get_active_tasks_by_session(
        self,
        session_id: uuid.UUID,
    ) -> list[TaskRead]:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(Task)
                .where(
                    Task.session_id == session_id,
                    Task.status.in_([TaskStatus.PENDING, TaskStatus.RUNNING]),
                )
                .order_by(desc(Task.created_at))
            )
            return [TaskRead.model_validate(t) for t in result.scalars().all()]
        finally:
            await self._close_session(session)

    async def get_tasks_by_session(
        self,
        session_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TaskRead]:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(Task)
                .where(Task.session_id == session_id)
                .order_by(desc(Task.created_at))
                .limit(limit)
                .offset(offset)
            )
            return [TaskRead.model_validate(t) for t in result.scalars().all()]
        finally:
            await self._close_session(session)
