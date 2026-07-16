"""
Session 路由
会话的 CRUD 和四维度心智配置管理
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from backend.core.unit_of_work import UnitOfWork
from backend.schemas.session import (
    SessionConfig,
    SessionConfigUpdate,
    SessionCreate,
    SessionRead,
)
from backend.schemas.user import UserRead

from ..dependencies import get_current_user, get_session_repo
from backend.repositories import SessionRepository

router = APIRouter(prefix="/sessions", tags=["Sessions"])


@router.get("/my", response_model=list[SessionRead])
async def list_my_sessions(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[SessionRepository, Depends(get_session_repo)],
):
    """获取当前用户的所有会话"""
    sessions = await repo.list_by_user(current_user.id)
    return sessions


@router.post("", response_model=SessionRead)
async def create_session(
    data: SessionCreate,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[SessionRepository, Depends(get_session_repo)],
):
    """创建新会话（自动关联当前用户）"""
    config = data.config.model_dump() if data.config else {}
    session = await repo.create(
        {"user_id": current_user.id, "config": config}
    )
    return session


@router.get("/{session_id}", response_model=SessionRead)
async def get_session(
    session_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """获取会话详情（归属校验与读取在同一事务）"""
    async with UnitOfWork() as uow:
        session = await uow.sessions.get_by_id(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        # 用户隔离检查
        if getattr(session, "user_id", None) and session.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Access denied")
        return session


@router.put("/{session_id}/config", response_model=SessionRead)
async def update_session_config(
    session_id: uuid.UUID,
    data: SessionConfigUpdate,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """更新会话的四维度心智配置（归属校验与更新在同一事务）"""
    async with UnitOfWork() as uow:
        session = await uow.sessions.get_by_id(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        if getattr(session, "user_id", None) and session.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Access denied")
        return await uow.sessions.update_config(
            session_id, data.config.model_dump()
        )


@router.delete("/{session_id}")
async def delete_session(
    session_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """删除会话（归属校验与删除在同一事务）"""
    async with UnitOfWork() as uow:
        session = await uow.sessions.get_by_id(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        if getattr(session, "user_id", None) and session.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Access denied")
        success = await uow.sessions.delete(session_id)
        if not success:
            raise HTTPException(status_code=404, detail="Session not found")
        return {"deleted": True}
