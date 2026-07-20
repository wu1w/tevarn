"""
Skill Store API 路由

提供多源 skill 商店访问：
- GET  /api/skills/store/sources       列出可用源
- GET  /api/skills/store/list          跨源列出 skills
- GET  /api/skills/store/{source}/{id} 获取单个 skill 详情
- POST /api/skills/store/install       安装 skill（下载 SKILL.md）
- POST /api/skills/store/uninstall     卸载 skill
- GET  /api/skills/store/installed     列出已安装的 prompt-skill
- POST /api/skills/store/refresh       刷新缓存
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.api.dependencies import get_current_user
from backend.schemas.skill_store import (
    SkillSource,
    SkillStoreQuery,
    SkillStoreResponse,
    UnifiedSkill,
)
from backend.schemas.user import UserRead
from backend.services.skill_store import get_skill_store_service
from backend.services.skill_store.skill_md_storage import (
    get_skill_md_downloader,
    get_skill_md_storage,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/store", tags=["skill-store"])


class InstallRequest(BaseModel):
    source: SkillSource
    skill_id: str


class UninstallRequest(BaseModel):
    source: SkillSource
    skill_id: str


class InstalledSkill(BaseModel):
    source: str
    name: str
    path: str
    size: int


class InstallResponse(BaseModel):
    success: bool
    skill_id: str
    source: str
    path: str = ""
    error: str = ""


@router.get("/sources", response_model=list[dict])
async def list_sources(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """列出所有可用的 skill 源"""
    svc = get_skill_store_service()
    sources = []
    for src in svc.available_sources():
        fetcher = svc._fetchers.get(src)
        sources.append({
            "id": src,
            "display_name": getattr(fetcher, "display_name", src) if fetcher else src,
        })
    return sources


@router.get("/list", response_model=SkillStoreResponse)
async def list_skills(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    source: SkillSource | None = Query(None),
    search: str = Query(""),
    topic: str = Query(""),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """跨源列出 skills"""
    svc = get_skill_store_service()
    query = SkillStoreQuery(
        source=source,
        search=search,
        topic=topic,
        limit=limit,
        offset=offset,
    )
    return await svc.list_skills(query)


@router.get("/skill/{source}/{skill_id}", response_model=UnifiedSkill)
async def get_skill_detail(
    source: SkillSource,
    skill_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """获取单个 skill 详情"""
    svc = get_skill_store_service()
    skill = await svc.get_skill(skill_id, source=source)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill not found: {source}/{skill_id}")
    return skill


@router.post("/install", response_model=InstallResponse)
async def install_skill(
    payload: InstallRequest,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """安装 skill：下载 SKILL.md 并存到本地"""
    svc = get_skill_store_service()
    skill = await svc.get_skill(payload.skill_id, source=payload.source)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill not found: {payload.source}/{payload.skill_id}")

    # Takton 原生 skill 走老的 import 流程（schema dict）
    if payload.source == "takton":
        raise HTTPException(
            status_code=400,
            detail="Takton 原生 skill 请使用 /api/skills/community/import 接口",
        )

    storage = get_skill_md_storage()
    downloader = get_skill_md_downloader()

    try:
        content = await downloader.download(skill)
    except Exception as e:
        return InstallResponse(
            success=False,
            skill_id=payload.skill_id,
            source=payload.source,
            error=f"Download failed: {e}",
        )

    try:
        # 用解析后的 skill.id（可能含 owner/slug 格式）作为存储 key，避免歧义覆盖
        storage_key = skill.id if "/" in skill.id else payload.skill_id
        path = storage.write(payload.source, storage_key, content)
    except Exception as e:
        return InstallResponse(
            success=False,
            skill_id=payload.skill_id,
            source=payload.source,
            error=f"Write failed: {e}",
        )

    return InstallResponse(
        success=True,
        skill_id=payload.skill_id,
        source=payload.source,
        path=str(path),
    )


@router.post("/uninstall", response_model=InstallResponse)
async def uninstall_skill(
    payload: UninstallRequest,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """卸载 skill"""
    storage = get_skill_md_storage()
    removed = storage.remove(payload.source, payload.skill_id)
    return InstallResponse(
        success=removed,
        skill_id=payload.skill_id,
        source=payload.source,
        error="" if removed else "Skill not installed",
    )


@router.get("/installed", response_model=list[InstalledSkill])
async def list_installed(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """列出已安装的 prompt-skill"""
    storage = get_skill_md_storage()
    return storage.list_installed()


class ActivePromptSkill(BaseModel):
    source: str
    name: str
    display_name: str
    description: str
    path: str
    size: int


class InjectionPreview(BaseModel):
    mode: str
    query: str
    summary_skills: list[str]
    full_skills: list[str]
    scores: dict[str, float]
    block_chars: int
    threshold: float
    max_full: int
    sample_preview: str


@router.get("/active", response_model=list[ActivePromptSkill])
async def list_active_prompt_skills(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """列出当前已安装、会被纳入 system prompt 目录的 prompt-skills。

    实际是否注入「全文」取决于 prompt_skill_mode 与当前 user_input 相关性，
    可用 GET /api/skills/store/injection-preview?q=... 预览。
    """
    from backend.services.skill_store.prompt_skill_loader import (
        get_prompt_skill_loader,
    )

    skills = get_prompt_skill_loader().list_installed()
    return [
        ActivePromptSkill(
            source=s.source,
            name=s.name,
            display_name=s.display_name,
            description=s.description,
            path=s.path,
            size=s.size,
        )
        for s in skills
    ]


@router.get("/injection-preview", response_model=InjectionPreview)
async def preview_prompt_skill_injection(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    q: str = Query("", description="模拟的用户输入，用于 auto 相关度匹配"),
    mode: str | None = Query(None, description="summary|auto|full，默认读 settings"),
):
    """预览当前注入策略：对给定 q，哪些 skill 会注入全文。"""
    from backend.core.config import settings
    from backend.services.skill_store.prompt_skill_loader import (
        get_prompt_skill_loader,
    )

    loader = get_prompt_skill_loader()
    block, plan = loader.build_injection_block(q or "", mode=mode)
    sample = block[:1200] + ("…" if len(block) > 1200 else "")
    return InjectionPreview(
        mode=plan.mode,
        query=q or "",
        summary_skills=plan.summary_skills,
        full_skills=plan.full_skills,
        scores=plan.scores,
        block_chars=plan.block_chars,
        threshold=float(getattr(settings, "prompt_skill_match_threshold", 0.85) or 0.85),
        max_full=int(getattr(settings, "prompt_skill_max_full", 2) or 2),
        sample_preview=sample,
    )


@router.post("/refresh")
async def refresh_cache(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    source: SkillSource | None = Query(None),
):
    """刷新缓存"""
    svc = get_skill_store_service()
    await svc.invalidate_cache(source)
    return {"refreshed": source or "all"}
