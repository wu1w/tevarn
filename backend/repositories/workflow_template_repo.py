"""
WorkflowTemplate Repository 接口与实现
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.workflow_template import WorkflowTemplate
from backend.repositories.base import AsyncBaseRepository, BaseRepository


class WorkflowTemplateRepository(BaseRepository):
    """WorkflowTemplate 仓库接口"""

    async def list_by_category(self, category: str) -> list[WorkflowTemplate]:
        raise NotImplementedError

    async def list_builtins(self) -> list[WorkflowTemplate]:
        raise NotImplementedError


class AsyncWorkflowTemplateRepository(AsyncBaseRepository, WorkflowTemplateRepository):
    """WorkflowTemplate 异步仓库实现"""

    async def get_by_id(self, id: Any) -> WorkflowTemplate | None:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(WorkflowTemplate).where(WorkflowTemplate.id == id)
            )
            return result.scalar_one_or_none()
        finally:
            await self._close_session(session)

    async def create(self, data: dict[str, Any]) -> WorkflowTemplate:
        session = await self._get_session()
        try:
            obj = WorkflowTemplate(**data)
            session.add(obj)
            await self._maybe_commit(session)
            await session.refresh(obj)
            return obj
        finally:
            await self._close_session(session)

    async def update(self, id: Any, data: dict[str, Any]) -> WorkflowTemplate | None:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(WorkflowTemplate).where(WorkflowTemplate.id == id)
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
        session = await self._get_session()
        try:
            result = await session.execute(
                select(WorkflowTemplate).where(WorkflowTemplate.id == id)
            )
            obj = result.scalar_one_or_none()
            if not obj:
                return False
            await session.delete(obj)
            await self._maybe_commit(session)
            return True
        finally:
            await self._close_session(session)

    async def list_by_user(self, user_id: uuid.UUID) -> list[WorkflowTemplate]:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(WorkflowTemplate)
                .where(WorkflowTemplate.user_id == user_id)
                .order_by(WorkflowTemplate.name)
            )
            return list(result.scalars().all())
        finally:
            await self._close_session(session)

    async def list_all(self) -> list[WorkflowTemplate]:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(WorkflowTemplate).order_by(WorkflowTemplate.name)
            )
            return list(result.scalars().all())
        finally:
            await self._close_session(session)

    async def list_by_category(self, category: str) -> list[WorkflowTemplate]:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(WorkflowTemplate)
                .where(WorkflowTemplate.category == category)
                .order_by(WorkflowTemplate.name)
            )
            return list(result.scalars().all())
        finally:
            await self._close_session(session)

    async def list_builtins(self) -> list[WorkflowTemplate]:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(WorkflowTemplate)
                .where(WorkflowTemplate.is_builtin.is_(True))
                .order_by(WorkflowTemplate.name)
            )
            return list(result.scalars().all())
        finally:
            await self._close_session(session)

    async def list_categories(self) -> list[dict[str, Any]]:
        """按分类统计模板数量"""
        from sqlalchemy import func
        session = await self._get_session()
        try:
            result = await session.execute(
                select(WorkflowTemplate.category, func.count(WorkflowTemplate.id))
                .group_by(WorkflowTemplate.category)
                .order_by(WorkflowTemplate.category)
            )
            return [{"category": row[0], "count": row[1]} for row in result.all()]
        finally:
            await self._close_session(session)
