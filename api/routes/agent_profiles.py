"""
Agent Profiles 路由
Agent 多角色配置 API

新创建的配置归属当前用户；user_id 为 None 的数据为全局共享，
所有登录用户均可查看，但只有超级管理员可修改，私有配置仅所有者和超级管理员可操作。
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError

from backend.repositories import AgentProfileRepository
from backend.schemas.agent_profile import (
    AgentProfileCreate,
    AgentProfileRead,
    AgentProfileUpdate,
)
from backend.schemas.user import UserRead

from ..dependencies import get_current_user, get_agent_profile_repo

router = APIRouter(prefix="/agent-profiles", tags=["Agent Profiles"])


def _is_profile_owner_or_admin(profile: Any, user: UserRead) -> bool:
    """私有配置仅所有者和超级管理员可操作；全局配置仅超级管理员可修改。"""
    if profile.user_id is None:
        return user.is_superuser
    return profile.user_id == user.id or user.is_superuser


@router.get("", response_model=list[AgentProfileRead])
async def list_profiles(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[AgentProfileRepository, Depends(get_agent_profile_repo)],
):
    """列出当前用户可见的 Agent 配置"""
    return await repo.list_by_user(current_user.id) or []


@router.post("", response_model=AgentProfileRead)
async def create_profile(
    data: AgentProfileCreate,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[AgentProfileRepository, Depends(get_agent_profile_repo)],
):
    """创建 Agent 配置"""
    payload = data.model_dump()
    payload["user_id"] = current_user.id
    try:
        return await repo.create(payload)
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail="Agent profile name already exists",
        ) from exc


@router.get("/{profile_id}", response_model=AgentProfileRead)
async def get_profile(
    profile_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[AgentProfileRepository, Depends(get_agent_profile_repo)],
):
    """获取 Agent 配置详情"""
    profile = await repo.get_by_id(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    if not _is_profile_owner_or_admin(profile, current_user):
        raise HTTPException(status_code=403, detail="Access denied")
    return profile


@router.put("/{profile_id}", response_model=AgentProfileRead)
async def update_profile(
    profile_id: uuid.UUID,
    data: AgentProfileUpdate,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[AgentProfileRepository, Depends(get_agent_profile_repo)],
):
    """更新 Agent 配置"""
    profile = await repo.get_by_id(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    if not _is_profile_owner_or_admin(profile, current_user):
        raise HTTPException(status_code=403, detail="Access denied")

    if data.name is not None:
        existing = await repo.get_by_name(data.name)
        if existing is not None and existing.id != profile_id:
            raise HTTPException(status_code=409, detail="Agent profile name already exists")

    try:
        profile = await repo.update(profile_id, data.model_dump(exclude_unset=True))
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Agent profile name already exists") from exc
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile


@router.delete("/{profile_id}")
async def delete_profile(
    profile_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[AgentProfileRepository, Depends(get_agent_profile_repo)],
):
    """删除 Agent 配置"""
    profile = await repo.get_by_id(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    if not _is_profile_owner_or_admin(profile, current_user):
        raise HTTPException(status_code=403, detail="Access denied")
    success = await repo.delete(profile_id)
    if not success:
        raise HTTPException(status_code=404, detail="Profile not found")
    return {"deleted": True}


@router.post("/{profile_id}/default")
async def set_default_profile(
    profile_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[AgentProfileRepository, Depends(get_agent_profile_repo)],
):
    """设为默认配置"""
    profile = await repo.get_by_id(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    if not _is_profile_owner_or_admin(profile, current_user):
        raise HTTPException(status_code=403, detail="Access denied")
    profile = await repo.set_default(profile_id, current_user.id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    return {"default": True}
