"""
SubAgent 路由
子代理集群管理 API：CRUD + 模型池 Inventory + 运行时模型解析
"""

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from backend.repositories.sub_agent_repo import AsyncSubAgentRepository
from backend.schemas.sub_agent import (
    SubAgentCreate,
    SubAgentRead,
    SubAgentUpdate,
    LLMConfig,
    ModelInventoryResponse,
    ModelInventoryItem,
)
from backend.schemas.user import UserRead

from ..dependencies import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/subagents", tags=["SubAgents"])

_agent_repo = AsyncSubAgentRepository()


async def get_agent_repo() -> AsyncSubAgentRepository:
    return _agent_repo


# ────────────────── 模型池 Inventory ──────────────────

@router.get("/model-inventory", response_model=ModelInventoryResponse)
async def get_model_inventory(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """
    获取模型池 Inventory — 从 Settings 中读取所有已配置的 provider/model，
    返回统一列表供子代理选择。
    """
    try:
        from backend.core.config import settings
    except Exception:
        settings = None

    inventory: list[ModelInventoryItem] = []

    if settings:
        providers = getattr(settings, 'providers', None)
        if providers and isinstance(providers, dict):
            for pid, pconf in providers.items():
                if not isinstance(pconf, dict):
                    continue
                models = pconf.get("models", [])
                if isinstance(models, str):
                    models = [m.strip() for m in models.split(",") if m.strip()]
                connected = pconf.get("connected", True)
                for mname in models:
                    ref = f"{pid}/{mname}"
                    status = "available"
                    # 判断是否为当前活跃模型
                    if hasattr(settings, "active_model") and settings.active_model == ref:
                        status = "active"
                    elif hasattr(settings, "default_model") and settings.default_model == ref:
                        status = "default"
                    inventory.append(ModelInventoryItem(
                        ref=ref,
                        provider_id=pid,
                        provider_name=pconf.get("name", pid),
                        provider_icon=pconf.get("icon", "🔌"),
                        model_name=mname,
                        status=status,
                        connected=connected,
                    ))

    # 如果 Settings 没有提供，返回空列表
    if not inventory:
        # Fallback: 尝试从环境变量读取
        import os
        active = os.getenv("TAKTON_ACTIVE_MODEL", "")
        if active:
            parts = active.split("/", 1)
            inventory.append(ModelInventoryItem(
                ref=active,
                provider_id=parts[0] if len(parts) > 1 else "default",
                provider_name=parts[0] if len(parts) > 1 else "Default",
                provider_icon="🔌",
                model_name=parts[-1],
                status="active",
                connected=True,
            ))

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
    obj = await repo.create({**data.model_dump(), "user_id": current_user.id, "is_builtin": False})
    logger.info(f"SubAgent created: {obj.id} ({obj.name})")
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
    if obj.is_builtin and data.model_dump(exclude_unset=True):
        # 内置模板只允许改 enabled
        allowed = {"enabled"}
        updates = {k: v for k, v in data.model_dump(exclude_unset=True).items() if k in allowed}
        if not updates:
            raise HTTPException(status_code=400, detail="Cannot modify builtin template (only 'enabled' allowed)")
        return await repo.update(agent_id, updates)
    if obj.user_id and obj.user_id != current_user.id and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Access denied")
    update_data = data.model_dump(exclude_unset=True)
    return await repo.update(agent_id, update_data)


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
    if obj.user_id and obj.user_id != current_user.id and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Access denied")
    await repo.delete(agent_id)
    logger.info(f"SubAgent deleted: {agent_id}")


# ────────────────── 运行时模型解析 ──────────────────

@router.get("/{agent_id}/resolve-model", response_model=LLMConfig)
async def resolve_model(
    agent_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: AsyncSubAgentRepository = Depends(get_agent_repo),
):
    """
    解析子代理的 model_ref → 实际 LLM 配置。
    如果 provider 不可用，降级到主 Agent active model。
    """
    obj = await repo.get_by_id(agent_id)
    if not obj:
        raise HTTPException(status_code=404, detail="SubAgent not found")

    model_ref = obj.model_ref
    resolved = False

    # 尝试从 Settings 解析
    try:
        from backend.core.config import settings
        providers = getattr(settings, 'providers', None)
        if providers and isinstance(providers, dict):
            parts = model_ref.split("/", 1)
            pid = parts[0] if len(parts) > 1 else ""
            mname = parts[-1]
            pconf = providers.get(pid, {})
            if pconf and pconf.get("connected", True):
                return LLMConfig(
                    provider=pid,
                    model=mname,
                    base_url=pconf.get("base_url", ""),
                    api_key="[REDACTED]",
                    temperature=obj.temperature or 0.3,
                    max_tokens=getattr(settings, "max_tokens", 4096),
                )
    except Exception as e:
        logger.warning(f"Failed to resolve model_ref '{model_ref}': {e}")

    # 降级到 active model
    if not resolved:
        try:
            from backend.core.config import settings
            active = getattr(settings, "llm_model", "") or getattr(settings, "active_model", "")
            if active:
                parts = active.split("/", 1)
                pid = parts[0] if len(parts) > 1 else "default"
                mname = parts[-1]
                pconf = (getattr(settings, "providers", {}) or {}).get(pid, {})
                return LLMConfig(
                    provider=pid,
                    model=mname,
                    base_url=pconf.get("base_url", ""),
                    api_key="[REDACTED]",
                    temperature=obj.temperature or 0.3,
                    max_tokens=getattr(settings, "max_tokens", 4096),
                    degraded=True,
                    original_ref=model_ref,
                )
        except Exception:
            pass

    raise HTTPException(status_code=503, detail=f"Cannot resolve model '{model_ref}' and no fallback available")
