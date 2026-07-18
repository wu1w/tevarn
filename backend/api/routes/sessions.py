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

from ..dependencies import get_current_user, get_session_repo, assert_session_owner
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
    """创建新会话（自动关联当前用户）

    快照当前 LLM 配置到 session.config.llm：会话锁定创建时的
    provider/model/base_url/api_key，之后全局配置变更不影响本会话
    （学 hermes：provider 配置与默认模型、与已发生会话解耦）。
    """
    from backend.core.config import settings as app_settings

    config = data.config.model_dump() if data.config else {}
    # 仅当未显式指定 llm 快照时才写入（避免覆盖前端显式传入）
    if "llm" not in config:
        cfg = app_settings.get_llm_config()
        # 「新会话默认模型」独立选项：若设置则覆盖当前 provider 配置的模型
        # （学 hermes model.default：provider 配置与新会话默认模型解耦）
        default_model = (getattr(app_settings, "default_llm_model", "") or "").strip()
        config["llm"] = {
            "provider": app_settings.llm_provider,
            "model": default_model or (getattr(cfg, "model", "") or ""),
            "base_url": getattr(cfg, "base_url", "") or "",
            "api_key": getattr(cfg, "api_key", None),
        }
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
        assert_session_owner(getattr(session, "user_id", None), current_user)
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
        assert_session_owner(getattr(session, "user_id", None), current_user)
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
        assert_session_owner(getattr(session, "user_id", None), current_user)
        success = await uow.sessions.delete(session_id)
        if not success:
            raise HTTPException(status_code=404, detail="Session not found")
        return {"deleted": True}


@router.get("/{session_id}/checkpoint")
async def get_session_checkpoint(
    session_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """查看 agent 断点 / Goal 续跑状态"""
    async with UnitOfWork() as uow:
        session = await uow.sessions.get_by_id(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        assert_session_owner(getattr(session, "user_id", None), current_user)
    from backend.agent.checkpoint import load_checkpoint
    from backend.agent.goal_state import get_goal, load_goal_from_db
    from backend.agent.resume import build_resume_prompt

    await load_goal_from_db(session_id)
    g = get_goal(session_id)
    cp = await load_checkpoint(session_id)
    prompt = await build_resume_prompt(session_id)
    return {
        "checkpoint": cp,
        "goal": g.to_dict() if g else None,
        "can_resume": prompt is not None,
        "resume_preview": (prompt[:500] + "…") if prompt and len(prompt) > 500 else prompt,
    }


@router.post("/{session_id}/resume")
async def resume_session(
    session_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """续跑未完成 Goal / checkpoint（同步等待本段结束）"""
    async with UnitOfWork() as uow:
        session = await uow.sessions.get_by_id(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        assert_session_owner(getattr(session, "user_id", None), current_user)

    from backend.agent.resume import build_resume_prompt, resume_session_agent

    prompt = await build_resume_prompt(session_id)
    if not prompt:
        return {"resumed": False, "detail": "nothing to resume", "content": None}

    content = await resume_session_agent(
        session_id,
        user_id=current_user.id,
        prompt=prompt,
    )
    return {"resumed": True, "content": content}
