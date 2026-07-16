"""
Task 路由
异步任务查询
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.core.unit_of_work import UnitOfWork
from backend.schemas.task import TaskRead
from backend.schemas.user import UserRead

from ..dependencies import get_current_user
from backend.repositories import TaskRepository

router = APIRouter(prefix="/sessions", tags=["Tasks"])


@router.get("/{session_id}/tasks", response_model=list[TaskRead])
async def get_tasks(
    session_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """分页获取会话的任务列表（归属校验与读取在同一事务）"""
    async with UnitOfWork() as uow:
        session = await uow.sessions.get_by_id(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        if getattr(session, "user_id", None) and session.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Access denied")
        return await uow.tasks.get_tasks_by_session(
            session_id, limit=limit, offset=offset
        )
