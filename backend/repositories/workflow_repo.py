"""
Workflow Repository 接口与实现
"""

import uuid
from abc import abstractmethod
from typing import Any

from sqlalchemy import select

from backend.models.workflow import Workflow

from .base import AsyncBaseRepository, BaseRepository


class WorkflowRepository(BaseRepository):
    """Workflow 仓库接口"""

    @abstractmethod
    async def list_by_status(self, status: str) -> list[Any]:
        """按状态列出工作流"""
        raise NotImplementedError

    @abstractmethod
    async def update_status(self, workflow_id: uuid.UUID, status: str) -> Any | None:
        """更新工作流状态"""
        raise NotImplementedError


class AsyncWorkflowRepository(AsyncBaseRepository, WorkflowRepository):
    """基于 SQLAlchemy async session 的 Workflow 仓库实现"""


    async def get_by_id(self, id: Any) -> Workflow | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(Workflow).where(Workflow.id == id))
            return result.scalar_one_or_none()
        finally:
            await self._close_session(session)

    async def create(self, data: dict[str, Any]) -> Workflow:
        session = await self._get_session()
        try:
            wf = Workflow(**data)
            session.add(wf)
            await self._maybe_commit(session)
            await session.refresh(wf)
            return wf
        finally:
            await self._close_session(session)

    async def update(self, id: Any, data: dict[str, Any]) -> Workflow | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(Workflow).where(Workflow.id == id))
            wf = result.scalar_one_or_none()
            if not wf:
                return None
            for key, value in data.items():
                setattr(wf, key, value)
            await self._maybe_commit(session)
            await session.refresh(wf)
            return wf
        finally:
            await self._close_session(session)

    async def delete(self, id: Any) -> bool:
        session = await self._get_session()
        try:
            result = await session.execute(select(Workflow).where(Workflow.id == id))
            wf = result.scalar_one_or_none()
            if not wf:
                return False
            await session.delete(wf)
            await self._maybe_commit(session)
            return True
        finally:
            await self._close_session(session)

    async def get_by_id_for_user(self, id: Any, user_id: Any) -> Workflow | None:
        session = await self._get_session()
        try:
            uid = uuid.UUID(user_id) if isinstance(user_id, str) else user_id
            result = await session.execute(
                select(Workflow).where(Workflow.id == id, Workflow.user_id == uid)
            )
            return result.scalar_one_or_none()
        finally:
            await self._close_session(session)

    async def list_by_status(self, status: str) -> list[Workflow]:
        session = await self._get_session()
        try:
            if status:
                result = await session.execute(select(Workflow).where(Workflow.status == status))
            else:
                result = await session.execute(select(Workflow))
            return list(result.scalars().all())
        finally:
            await self._close_session(session)

    async def list_by_user(self, user_id: uuid.UUID) -> list[Workflow]:
        session = await self._get_session()
        try:
            result = await session.execute(select(Workflow).where(Workflow.user_id == user_id))
            return list(result.scalars().all())
        finally:
            await self._close_session(session)

    async def update_status(self, workflow_id: uuid.UUID, status: str) -> Workflow | None:
        return await self.update(workflow_id, {"status": status})
