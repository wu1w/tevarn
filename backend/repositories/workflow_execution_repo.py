"""
工作流执行历史仓库
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from backend.models.workflow_execution import WorkflowExecution

from .base import AsyncBaseRepository


class AsyncWorkflowExecutionRepository(AsyncBaseRepository):
    """基于 SQLAlchemy async session 的工作流执行历史仓库"""

    async def get_by_id(self, id: Any) -> WorkflowExecution | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(WorkflowExecution).where(WorkflowExecution.id == id))
            return result.scalar_one_or_none()
        finally:
            await self._close_session(session)

    async def create(self, data: dict[str, Any]) -> WorkflowExecution:
        session = await self._get_session()
        try:
            execution = WorkflowExecution(**data)
            session.add(execution)
            await self._maybe_commit(session)
            await session.refresh(execution)
            return execution
        finally:
            await self._close_session(session)

    async def list_by_workflow(
        self,
        workflow_id: uuid.UUID,
        limit: int = 50,
    ) -> list[WorkflowExecution]:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(WorkflowExecution)
                .where(WorkflowExecution.workflow_id == workflow_id)
                .order_by(WorkflowExecution.created_at.desc())
                .limit(limit)
            )
            return list(result.scalars().all())
        finally:
            await self._close_session(session)

    async def finish(
        self,
        execution_id: uuid.UUID,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> WorkflowExecution | None:
        session = await self._get_session()
        try:
            result_q = await session.execute(
                select(WorkflowExecution).where(WorkflowExecution.id == execution_id)
            )
            execution = result_q.scalar_one_or_none()
            if not execution:
                return None
            execution.status = status
            execution.finished_at = datetime.now(timezone.utc)
            if result is not None:
                execution.result = result
            if error is not None:
                execution.error = error
            if execution.started_at and execution.finished_at:
                # SQLite 重读可能是 naive；统一按 UTC 再相减，避免 TypeError
                def _aware(dt):
                    if dt is None:
                        return None
                    if getattr(dt, "tzinfo", None) is None:
                        return dt.replace(tzinfo=timezone.utc)
                    return dt.astimezone(timezone.utc)

                try:
                    sa, fa = _aware(execution.started_at), _aware(execution.finished_at)
                    if sa and fa:
                        execution.duration_ms = int((fa - sa).total_seconds() * 1000)
                except Exception:
                    execution.duration_ms = None
            await self._maybe_commit(session)
            await session.refresh(execution)
            return execution
        finally:
            await self._close_session(session)
