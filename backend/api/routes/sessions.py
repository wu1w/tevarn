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

from ..dependencies import get_current_user, get_session_repo, get_setting_repo, assert_session_owner
from backend.repositories import SessionRepository, SettingRepository

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
    setting_repo: Annotated[SettingRepository, Depends(get_setting_repo)],
):
    """创建新会话（自动关联当前用户）

    快照完整 LLM 配置到 session.config.llm（provider_id + base_url + key + model）。
    「新会话默认模型」从 catalog 反查真实供应商，禁止只改 model 名沿用错误 base_url，
    也禁止冒出空壳 custom。
    """
    from backend.core.config import settings as app_settings
    from backend.core import model_catalog as model_catalog_mod
    from backend.core import model_gen_params as gen_params_mod

    config = data.config.model_dump() if data.config else {}
    if "llm" not in config:
        cfg = app_settings.get_llm_config()
        default_model = (getattr(app_settings, "default_llm_model", "") or "").strip()
        catalog = await model_catalog_mod.load_catalog(setting_repo)
        cleaned = model_catalog_mod.prune_orphan_providers(catalog)
        if cleaned != catalog:
            try:
                await model_catalog_mod.save_catalog(setting_repo, cleaned)
            except Exception:
                pass
        catalog = cleaned

        snap = model_catalog_mod.resolve_new_session_llm_snapshot(
            catalog,
            default_llm_model=default_model,
            fallback_provider=app_settings.llm_provider,
            fallback_model=getattr(cfg, "model", "") or "",
            fallback_base_url=getattr(cfg, "base_url", "") or "",
            fallback_api_key=getattr(cfg, "api_key", None),
            temperature=getattr(cfg, "temperature", None),
            max_tokens=getattr(cfg, "max_tokens", None),
            context_window=getattr(app_settings, "context_window", None),
        )
        try:
            params = await gen_params_mod.get_params(
                setting_repo,
                str(snap.get("provider_id") or ""),
                str(snap.get("model") or ""),
            )
            if params:
                if params.get("temperature") is not None:
                    snap["temperature"] = params["temperature"]
                if params.get("max_tokens") is not None:
                    snap["max_tokens"] = params["max_tokens"]
                if params.get("context_window") is not None:
                    snap["context_window"] = params["context_window"]
        except Exception:
            pass
        config["llm"] = snap
    session = await repo.create({"user_id": current_user.id, "config": config})
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
