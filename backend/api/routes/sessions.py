"""
Session 路由
会话的 CRUD 和四维度心智配置管理
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from backend.core.unit_of_work import UnitOfWork
from backend.repositories import SessionRepository, SettingRepository
from backend.schemas.session import (
    ContactSessionOpen,
    SessionConfig,
    SessionConfigUpdate,
    SessionCreate,
    SessionRead,
)
from backend.schemas.user import UserRead

from ..dependencies import (
    assert_session_owner,
    get_current_user,
    get_session_repo,
    get_setting_repo,
)

router = APIRouter(prefix="/sessions", tags=["Sessions"])


def _is_workforce_config(cfg: dict | None) -> bool:
    if not isinstance(cfg, dict):
        return False
    if cfg.get("source") == "workforce":
        return True
    if cfg.get("workforce") is True:
        return True
    if cfg.get("workforce_identity_id"):
        return True
    return False


def _default_contact_identity(name: str) -> str:
    low = name.lower()
    is_steward = (
        any(h in name for h in ("CEO", "CTO", "管家", "总裁", "小白"))
        or low in ("ceo", "cto", "steward")
        or "steward" in low
        or "chief" in low
    )
    if is_steward:
        return (
            f"You are {name}, the company steward (大管家/CEO). The user is your boss. "
            "When given work: (1) analyze and break it down, (2) use crew_steward "
            "list/hire/assign to hand tasks to real employees (inbox work orders), "
            "(3) do NOT spawn temporary subagents. You orchestrate; employees execute. "
            "Be concise; report who got which ticket. Multi-person work may open a project group."
        )
    return (
        f"You are {name}, a member of this company's AI workforce. "
        "Speak and act in character. The user is your boss — be concise, "
        "report progress, and escalate risks."
    )


@router.get("/my", response_model=list[SessionRead])
async def list_my_sessions(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[SessionRepository, Depends(get_session_repo)],
    kind: str | None = None,
):
    """获取当前用户会话。kind=human 排除 workforce 工单会话（IM 聊天列表）。"""
    sessions = await repo.list_by_user(current_user.id)
    if (kind or "").strip().lower() == "human":
        sessions = [
            s
            for s in sessions
            if not _is_workforce_config(s.config if isinstance(s.config, dict) else {})
        ]
    return sessions


@router.get("/active-ids", response_model=list[str])
async def list_active_session_ids(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """有活跃 WS 连接或运行中 agent 的 session id 列表。

    前端「空白会话自动清理」必须用此接口兜底：流式运行中消息可能尚未落库，
    仅按 DB 消息判空白会误删活跃会话（已发生事故：运行中切页后会话被清，
    回来报 Session not found）。
    """
    from backend.api.websocket import manager as ws_manager

    return sorted(ws_manager.active_session_ids())


@router.post("/contact", response_model=SessionRead)
async def open_contact_session(
    data: ContactSessionOpen,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[SessionRepository, Depends(get_session_repo)],
    setting_repo: Annotated[SettingRepository, Depends(get_setting_repo)],
):
    """企业 IM：一人一会话。按 contact_agent 名 find-or-create，不复用 workforce。"""
    name = (data.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")

    existing = await repo.list_by_user(current_user.id)
    candidates = []
    for s in existing:
        cfg = s.config if isinstance(s.config, dict) else {}
        if _is_workforce_config(cfg):
            continue
        if str(cfg.get("contact_agent") or "").strip() == name:
            candidates.append(s)
    if candidates:
        candidates.sort(key=lambda x: x.updated_at or x.created_at, reverse=True)
        return candidates[0]

    identity_text = (data.identity_text or "").strip() or _default_contact_identity(name)
    create_body = SessionCreate(
        config=SessionConfig(
            contact_agent=name,
            identity=identity_text,
            source="human_dm",
        )
    )
    return await create_session(create_body, current_user, repo, setting_repo)


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
    from backend.core import model_catalog as model_catalog_mod
    from backend.core import model_gen_params as gen_params_mod
    from backend.core.config import settings as app_settings

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
        # 键级合并：只覆盖用户提交的四维度键，保留 _goal/_agent_checkpoint/llm 等内部键
        return await uow.sessions.merge_config_keys(
            session_id, data.config.model_dump()
        )


@router.delete("/{session_id}")
async def delete_session(
    session_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    force: bool = False,
):
    """删除会话（归属校验与删除在同一事务）。

    活跃保护（纵深防御）：WS 连接中或 agent 运行中的会话默认拒删（409），
    防止自动清理逻辑误删运行中会话（流式消息未落库时按内容判空白会误杀）。
    用户显式删除时前端带 force=true 放行。
    """
    from backend.api.websocket import manager as ws_manager

    if not force and session_id in {
        uuid.UUID(s) for s in ws_manager.active_session_ids()
    }:
        raise HTTPException(
            status_code=409,
            detail="Session is active (connected or running), cannot delete",
        )
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
