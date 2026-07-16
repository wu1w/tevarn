"""
Message 路由
历史消息查询
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.core.unit_of_work import UnitOfWork
from backend.schemas.message import MessageRead
from backend.schemas.user import UserRead

from ..dependencies import get_current_user
from backend.repositories import MessageRepository

router = APIRouter(prefix="/sessions", tags=["Messages"])


@router.get("/{session_id}/messages", response_model=list[MessageRead])
async def get_messages(
    session_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[str, Query(max_length=200)] = "",
):
    """分页获取会话的历史消息（默认最近 limit 条；支持 q 全文搜索）"""
    async with UnitOfWork() as uow:
        session = await uow.sessions.get_by_id(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        if getattr(session, "user_id", None) and session.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Access denied")
        if q:
            return await uow.messages.search_messages(
                session_id, q, limit=limit, offset=offset
            )
        return await uow.messages.get_history_by_session(
            session_id, limit=limit, offset=offset
        )
