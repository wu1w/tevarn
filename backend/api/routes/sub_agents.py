"""
SubAgent 路由
子代理集群管理 API：CRUD + 模型池 Inventory + 运行时模型解析
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from backend.repositories.sub_agent_repo import AsyncSubAgentRepository
from backend.schemas.sub_agent import (
    LLMConfig,
    ModelInventoryItem,
    ModelInventoryResponse,
    SubAgentCreate,
    SubAgentRead,
    SubAgentUpdate,
)
from backend.schemas.user import UserRead

from ..dependencies import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/subagents", tags=["SubAgents"])

_agent_repo = AsyncSubAgentRepository()


async def get_agent_repo() -> AsyncSubAgentRepository:
    return _agent_repo


# ────────────────── 模型池 Inventory ──────────────────

async def _build_inventory_from_catalog() -> list[ModelInventoryItem]:
    """从 llm_model_catalog 展平模型池，供子代理选择。

    优先用 cached_models；若缓存为空且供应商已连接，则 live 拉一次并写回缓存。
    """
    inventory: list[ModelInventoryItem] = []
    try:
        from backend.core import model_catalog as model_catalog_mod
        from backend.repositories.setting_repo import AsyncSettingRepository

        repo = AsyncSettingRepository()
        catalog = await model_catalog_mod.load_catalog(repo)
    except Exception as e:
        logger.warning("load model catalog for inventory failed: %s", e)
        catalog = {}
        repo = None  # type: ignore
        model_catalog_mod = None  # type: ignore

    active_pid = str(catalog.get("active_provider_id") or "")
    active_model = str(catalog.get("active_model") or "")
    default_pid = str(catalog.get("default_provider_id") or active_pid)
    default_model = str(catalog.get("default_model") or active_model)
    fallback_pid = str(catalog.get("fallback_provider_id") or "")
    fallback_model = str(catalog.get("fallback_model") or "")

    providers = catalog.get("providers") or []
    if not isinstance(providers, list):
        providers = []

    catalog_dirty = False

    for p in providers:
        if not isinstance(p, dict):
            continue
        if p.get("enabled") is False:
            continue
        pid = str(p.get("id") or "").strip()
        if not pid:
            continue
        pname = str(p.get("name") or pid)
        picon = str(p.get("icon") or "🤖")
        disabled = set(p.get("disabled_models") or [])

        models: list[str] = []
        if model_catalog_mod is not None:
            models = model_catalog_mod.provider_models_for_display(
                p,
                global_active_provider_id=active_pid,
                global_active_model=active_model,
            )
        else:
            cached = p.get("cached_models") or []
            if isinstance(cached, list) and cached:
                models = [str(m).strip() for m in cached if str(m).strip()]
            am = str(p.get("active_model") or "").strip()
            if am and am not in models:
                models.insert(0, am)
            if pid == active_pid and active_model and active_model not in models:
                models.insert(0, active_model)

        has_key = bool(p.get("llm_api_key") or p.get("has_api_key"))
        connected = has_key or str(p.get("llm_provider") or "").lower() in {
            "ollama",
            "openai-compatible",
            "local",
            "custom",
        } or bool(p.get("llm_base_url"))

        # 缓存空且已连接 → 尝试 live 拉并落盘
        if (
            not models
            and connected
            and repo is not None
            and model_catalog_mod is not None
        ):
            try:
                from backend.api.routes.settings import fetch_provider_models

                listed = await fetch_provider_models(
                    str(p.get("llm_provider") or "openai-compatible"),
                    str(p.get("llm_base_url") or ""),
                    str(p.get("llm_api_key") or ""),
                )
                live = [
                    str(m).strip()
                    for m in (listed.get("models") or [])
                    if str(m).strip()
                ]
                if listed.get("ok") and live:
                    catalog = model_catalog_mod.set_provider_cached_models(
                        catalog,
                        pid,
                        live,
                        active_model=(p.get("active_model") or None),
                    )
                    catalog_dirty = True
                    p = next(
                        (x for x in catalog["providers"] if x["id"] == pid),
                        p,
                    )
                    models = model_catalog_mod.provider_models_for_display(
                        p,
                        global_active_provider_id=active_pid,
                        global_active_model=active_model,
                    )
            except Exception as e:
                logger.warning("live fetch models for inventory %s failed: %s", pid, e)

        if not models:
            continue

        for mname in models:
            if mname in disabled:
                continue
            ref = f"{pid}/{mname}"
            status = "available"
            if pid == active_pid and mname == active_model:
                status = "active"
            elif pid == default_pid and mname == default_model:
                status = "default"
            elif pid == fallback_pid and mname == fallback_model:
                status = "fallback"
            inventory.append(
                ModelInventoryItem(
                    ref=ref,
                    provider_id=pid,
                    provider_name=pname,
                    provider_icon=picon,
                    model_name=mname,
                    status=status,
                    connected=connected,
                )
            )

    if catalog_dirty and repo is not None and model_catalog_mod is not None:
        try:
            await model_catalog_mod.save_catalog(repo, catalog)
        except Exception as e:
            logger.warning("save inventory model cache failed: %s", e)

    # 兜底：catalog 为空时用 runtime settings
    if not inventory:
        try:
            from backend.core.config import settings

            model = str(getattr(settings, "llm_model", "") or "").strip()
            provider = str(getattr(settings, "llm_provider", "default") or "default")
            if model:
                ref = f"{provider}/{model}" if "/" not in model else model
                parts = ref.split("/", 1)
                inventory.append(
                    ModelInventoryItem(
                        ref=ref,
                        provider_id=parts[0],
                        provider_name=parts[0],
                        provider_icon="🤖",
                        model_name=parts[-1],
                        status="active",
                        connected=True,
                    )
                )
        except Exception:
            pass

    return inventory


@router.get("/model-inventory", response_model=ModelInventoryResponse)
async def get_model_inventory(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """获取模型池 Inventory — 从 Settings 模型目录展平。"""
    inventory = await _build_inventory_from_catalog()
    return ModelInventoryResponse(inventory=inventory)


# ────────────────── 子代理 CRUD ──────────────────

@router.get("", response_model=list[SubAgentRead])
async def list_sub_agents(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: AsyncSubAgentRepository = Depends(get_agent_repo),
):
    """列出所有子代理（内置 + 用户自定义）"""
    return await repo.list_all() or []


@router.get("/{agent_id}", response_model=SubAgentRead)
async def get_sub_agent(
    agent_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: AsyncSubAgentRepository = Depends(get_agent_repo),
):
    """获取单个子代理"""
    obj = await repo.get_by_id(agent_id)
    if not obj:
        raise HTTPException(status_code=404, detail="SubAgent not found")
    return obj


@router.post("", response_model=SubAgentRead, status_code=201)
async def create_sub_agent(
    data: SubAgentCreate,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: AsyncSubAgentRepository = Depends(get_agent_repo),
):
    """创建自定义子代理"""
    payload = {**data.model_dump(), "user_id": current_user.id, "is_builtin": False}
    obj = await repo.create(payload)
    logger.info("SubAgent created: %s (%s)", obj.id, obj.name)
    return obj


@router.put("/{agent_id}", response_model=SubAgentRead)
async def update_sub_agent(
    agent_id: uuid.UUID,
    data: SubAgentUpdate,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: AsyncSubAgentRepository = Depends(get_agent_repo),
):
    """更新子代理"""
    obj = await repo.get_by_id(agent_id)
    if not obj:
        raise HTTPException(status_code=404, detail="SubAgent not found")
    updates = data.model_dump(exclude_unset=True)
    if obj.is_builtin:
        # 内置模板允许改 enabled / 模型 / 提示词，方便集群使用
        allowed = {"enabled", "model_ref", "system_prompt", "temperature", "max_iterations", "enabled_toolsets"}
        updates = {k: v for k, v in updates.items() if k in allowed}
        if not updates:
            raise HTTPException(status_code=400, detail="Builtin template: nothing allowed to update")
    elif obj.user_id and obj.user_id != current_user.id and not getattr(current_user, "is_superuser", False):
        raise HTTPException(status_code=403, detail="Access denied")
    return await repo.update(agent_id, updates)


@router.delete("/{agent_id}", status_code=204)
async def delete_sub_agent(
    agent_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: AsyncSubAgentRepository = Depends(get_agent_repo),
):
    """删除子代理"""
    obj = await repo.get_by_id(agent_id)
    if not obj:
        raise HTTPException(status_code=404, detail="SubAgent not found")
    if obj.is_builtin:
        raise HTTPException(status_code=400, detail="Cannot delete builtin template")
    if obj.user_id and obj.user_id != current_user.id and not getattr(current_user, "is_superuser", False):
        raise HTTPException(status_code=403, detail="Access denied")
    await repo.delete(agent_id)
    logger.info("SubAgent deleted: %s", agent_id)


# ────────────────── 运行时模型解析 ──────────────────

@router.get("/{agent_id}/resolve-model", response_model=LLMConfig)
async def resolve_model(
    agent_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: AsyncSubAgentRepository = Depends(get_agent_repo),
):
    """解析子代理 model_ref → 实际 LLM 配置（密钥脱敏）。"""
    obj = await repo.get_by_id(agent_id)
    if not obj:
        raise HTTPException(status_code=404, detail="SubAgent not found")

    model_ref = obj.model_ref or ""
    parts = model_ref.split("/", 1)
    pid = parts[0] if len(parts) > 1 else ""
    mname = parts[-1] if parts else ""

    try:
        from backend.core import model_catalog as model_catalog_mod
        from backend.repositories.setting_repo import AsyncSettingRepository

        catalog = await model_catalog_mod.load_catalog(AsyncSettingRepository())
        for p in catalog.get("providers") or []:
            if not isinstance(p, dict):
                continue
            if str(p.get("id") or "") != pid:
                continue
            return LLMConfig(
                provider=str(p.get("llm_provider") or pid),
                model=mname,
                base_url=str(p.get("llm_base_url") or ""),
                api_key="[REDACTED]",
                temperature=float(obj.temperature or 0.3),
                max_tokens=4096,
                original_ref=model_ref,
            )
    except Exception as e:
        logger.warning("resolve model_ref %s failed: %s", model_ref, e)

    # 降级到 runtime active
    try:
        from backend.core.config import settings

        active = str(getattr(settings, "llm_model", "") or "")
        provider = str(getattr(settings, "llm_provider", "default") or "default")
        base = str(getattr(settings, "llm_base_url", "") or "")
        if active:
            return LLMConfig(
                provider=provider,
                model=active,
                base_url=base,
                api_key="[REDACTED]",
                temperature=float(obj.temperature or 0.3),
                max_tokens=4096,
                degraded=True,
                original_ref=model_ref,
            )
    except Exception:
        pass

    raise HTTPException(status_code=503, detail=f"Cannot resolve model '{model_ref}'")
