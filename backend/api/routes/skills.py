"""
Skill 路由
技能管理：内置 Skill、自定义 Skill、社区下载
"""

import json
import logging
import uuid
from typing import Annotated, Any

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError

logger = logging.getLogger(__name__)

from backend.core.config import settings
from backend.core.net_safety import UnsafeURLError, validate_public_url
from backend.repositories import SkillRepository
from backend.repositories.skill_repo import AsyncSkillRepository
from backend.schemas.skill import (
    CommunitySkillImport,
    SkillCreate,
    SkillRead,
    SkillToggle,
    SkillUpdate,
)
from backend.schemas.user import UserRead
from backend.skills import SkillRegistry

from ..dependencies import get_current_user, get_skill_repo, require_admin

router = APIRouter(prefix="/skills", tags=["Skills"])

# 默认社区 Skill 索引地址（可被 settings.community_skills_index_url 覆盖）
_DEFAULT_COMMUNITY_INDEX_URL = (
    "https://raw.githubusercontent.com/takton-ai/community-skills/main/index.json"
)


@router.get("", response_model=list[SkillRead])
async def list_skills(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[SkillRepository, Depends(get_skill_repo)],
    enabled_only: bool = Query(False, description="仅启用；默认列出全部以便管理进化草稿"),
):
    """列出 Skill（内置 + 自定义 + 进化；默认含未启用草稿）"""
    session_repo = repo
    if enabled_only and hasattr(repo, "get_active_skills"):
        db_skills = await repo.get_active_skills()
    else:
        # all rows
        try:
            from sqlalchemy import select
            from backend.models.skill import Skill
            from backend.database import get_db_context

            async with get_db_context() as session:
                result = await session.execute(select(Skill).order_by(Skill.name))
                db_skills = list(result.scalars().all())
        except Exception:
            db_skills = await repo.get_active_skills()

    db_names = {s.name for s in db_skills}
    for skill in SkillRegistry.get_all_skills():
        if skill.name not in db_names:
            db_skills.append(
                SkillRead(
                    id=uuid.uuid4(),
                    name=skill.name,
                    description=skill.description,
                    schema=skill.parameters,
                    enabled=True,
                    is_builtin=True,
                    handler="http",
                    handler_config={},
                    created_at=None,
                    updated_at=None,
                )
            )
    return db_skills


@router.get("/schema", response_model=list[dict[str, Any]])
async def get_skill_schemas(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """获取所有启用的 Skill 的 JSON Schema（供 LLM 使用）"""
    return SkillRegistry.get_tools_schema()


@router.post("", response_model=SkillRead)
async def create_skill(
    data: SkillCreate,
    current_user: Annotated[UserRead, Depends(require_admin)],
    repo: Annotated[SkillRepository, Depends(get_skill_repo)],
):
    """创建自定义 Skill（仅管理员）"""
    if data.name in SkillRegistry._skills:
        raise HTTPException(
            status_code=409, detail="A built-in skill with this name already exists"
        )
    existing = await repo.get_skill_by_name(data.name)
    if existing is not None:
        raise HTTPException(status_code=409, detail="Skill name already exists")

    payload = data.model_dump()
    payload["is_builtin"] = False
    try:
        return await repo.create(payload)
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Skill name already exists") from exc


@router.put("/{skill_id}", response_model=SkillRead)
async def update_skill(
    skill_id: uuid.UUID,
    data: SkillUpdate,
    current_user: Annotated[UserRead, Depends(require_admin)],
    repo: Annotated[SkillRepository, Depends(get_skill_repo)],
):
    """更新自定义 Skill（禁止修改内置 Skill）"""
    skill = await repo.get_by_id(skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    if skill.is_builtin:
        raise HTTPException(status_code=403, detail="Built-in skills cannot be edited")
    if data.name is not None:
        existing = await repo.get_skill_by_name(data.name)
        if existing is not None and existing.id != skill_id:
            raise HTTPException(status_code=409, detail="Skill name already exists")
    try:
        updated = await repo.update(skill_id, data.model_dump(exclude_unset=True))
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Skill name already exists") from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    return updated


@router.delete("/{skill_id}")
async def delete_skill(
    skill_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(require_admin)],
    repo: Annotated[SkillRepository, Depends(get_skill_repo)],
):
    """删除自定义 Skill（禁止删除内置 Skill）；进化 skill 同步删 evolution 资产。"""
    skill = await repo.get_by_id(skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    if skill.is_builtin:
        raise HTTPException(status_code=403, detail="Cannot delete built-in skills")
    skill_name = skill.name
    hc = getattr(skill, "handler_config", None) or {}
    is_evolved = bool(hc.get("evolution") or hc.get("source") == "evolution")
    success = await repo.delete(skill_id)
    if not success:
        raise HTTPException(status_code=404, detail="Skill not found")
    evo_deleted = 0
    if is_evolved:
        try:
            from backend.evolution.skill_sync import remove_evolution_assets_for_skill_name
            from backend.evolution.runtime_tools import unregister_evolved_tool

            evo_deleted = await remove_evolution_assets_for_skill_name(skill_name)
            unregister_evolved_tool(skill_name)
        except Exception:
            pass
    return {"deleted": True, "name": skill_name, "evolution_assets_removed": evo_deleted}


@router.put("/{skill_id}/toggle", response_model=SkillRead)
async def toggle_skill(
    skill_id: uuid.UUID,
    data: SkillToggle,
    current_user: Annotated[UserRead, Depends(require_admin)],
    repo: Annotated[SkillRepository, Depends(get_skill_repo)],
):
    """切换技能启用状态（仅管理员）"""
    skill = await repo.toggle_skill(skill_id, data.enabled)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    return skill


async def _fetch_community_skills(url: str | None) -> list[dict[str, Any]]:
    """从外部 URL 拉取社区 Skill 列表"""
    target_url = url or getattr(settings, "community_skills_index_url", None) or _DEFAULT_COMMUNITY_INDEX_URL
    if not target_url:
        raise HTTPException(status_code=400, detail="No community skills URL configured")

    try:
        validate_public_url(target_url)
    except UnsafeURLError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(target_url, timeout=30) as resp:
                data = await resp.json()
    except Exception as e:
        # 上游不可达/404/非 JSON 时降级为空列表，不向上抛 502
        logger.warning(f"Community skills fetch failed (degrading to empty): {e}")
        return []

    if not isinstance(data, list):
        raise HTTPException(status_code=400, detail="Expected a JSON array of skills")
    return data


@router.get("/community", response_model=list[SkillCreate])
async def list_community_skills(
    current_user: Annotated[UserRead, Depends(require_admin)],
    url: str | None = Query(None),
):
    """获取社区热门 Skill 列表（仅管理员）"""
    raw_items = await _fetch_community_skills(url)
    skills = []
    for item in raw_items:
        try:
            skills.append(SkillCreate(**item))
        except Exception:
            continue
    return skills


@router.post("/community/import")
async def import_community_skills(
    payload: CommunitySkillImport,
    current_user: Annotated[UserRead, Depends(require_admin)],
    repo: Annotated[SkillRepository, Depends(get_skill_repo)],
):
    """从社区索引中导入指定 Skill（仅管理员）"""
    raw_items = await _fetch_community_skills(payload.url)
    selected_set = set(payload.selected)

    imported = 0
    for item in raw_items:
        try:
            skill_create = SkillCreate(**item)
        except Exception:
            continue
        if skill_create.name not in selected_set:
            continue
        if skill_create.name in SkillRegistry._skills:
            continue
        existing = await repo.get_skill_by_name(skill_create.name)
        if existing is not None:
            continue
        data = skill_create.model_dump()
        data["is_builtin"] = False
        await repo.create(data)
        imported += 1

    return {"imported": imported}
