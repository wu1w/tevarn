"""
MCP Server 管理 API
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.dependencies import get_current_user, require_admin
from backend.repositories.mcp_server_repo import AsyncMCPServerRepository
from backend.schemas.mcp import (
    MCPServerConfig,
    MCPServerCreate,
    MCPServerStatus,
    MCPServerToggle,
    MCPServerUpdate,
)
from backend.schemas.user import UserRead
from backend.mcp_hub.service import get_mcp_status, load_mcp_tools

router = APIRouter(prefix="/mcp", tags=["MCP"])


_mcp_repo = AsyncMCPServerRepository()


def get_mcp_server_repo():
    return _mcp_repo


@router.get("", response_model=list[MCPServerConfig])
async def list_mcp_servers(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[AsyncMCPServerRepository, Depends(get_mcp_server_repo)],
):
    """列出所有 MCP Server 配置"""
    rows = await repo.list_all()
    return [MCPServerConfig.model_validate(r) for r in rows]


@router.post("", response_model=MCPServerConfig)
async def create_mcp_server(
    data: MCPServerCreate,
    current_user: Annotated[UserRead, Depends(require_admin)],
    repo: Annotated[AsyncMCPServerRepository, Depends(get_mcp_server_repo)],
    upsert: bool = Query(False, description="同名时更新已有配置（env/command/args 等）并热重连"),
):
    """创建 MCP Server（仅管理员）。upsert=true 时同名执行更新而非 409。"""
    existing = await repo.get_by_name(data.name)
    if existing:
        if not upsert:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "mcp_server_exists",
                    "message": f"MCP server '{data.name}' already exists",
                    "server_id": str(getattr(existing, "id", "") or existing.get("id", "")),
                    "hint": "retry with ?upsert=true to update config (env/command/args) in place",
                },
            )
        upd = MCPServerUpdate(**data.model_dump(exclude={"name"}))
        updated = await repo.update(existing.id, upd)
        await _hot_reload()
        return MCPServerConfig.model_validate(updated)
    server = await repo.create(data)
    return MCPServerConfig.model_validate(server)


async def _hot_reload() -> None:
    """配置变更后热重连 MCP 会话，让新 env/args 立即生效。"""
    try:
        await load_mcp_tools()
    except Exception:
        pass



@router.put("/{server_id}", response_model=MCPServerConfig)
async def update_mcp_server(
    server_id: uuid.UUID,
    data: MCPServerUpdate,
    current_user: Annotated[UserRead, Depends(require_admin)],
    repo: Annotated[AsyncMCPServerRepository, Depends(get_mcp_server_repo)],
):
    """更新 MCP Server（仅管理员），成功后热重连。"""
    updated = await repo.update(server_id, data)
    if updated is None:
        raise HTTPException(status_code=404, detail="MCP server not found")
    await _hot_reload()
    return MCPServerConfig.model_validate(updated)


@router.put("/{server_id}/toggle", response_model=MCPServerConfig)
async def toggle_mcp_server(
    server_id: uuid.UUID,
    data: MCPServerToggle,
    current_user: Annotated[UserRead, Depends(require_admin)],
    repo: Annotated[AsyncMCPServerRepository, Depends(get_mcp_server_repo)],
):
    """切换启用状态"""
    server = await repo.toggle(server_id, data.enabled)
    if server is None:
        raise HTTPException(status_code=404, detail="MCP server not found")
    return MCPServerConfig.model_validate(server)


@router.delete("/{server_id}")
async def delete_mcp_server(
    server_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(require_admin)],
    repo: Annotated[AsyncMCPServerRepository, Depends(get_mcp_server_repo)],
):
    """删除 MCP Server"""
    success = await repo.delete(server_id)
    if not success:
        raise HTTPException(status_code=404, detail="MCP server not found")
    return {"deleted": True}


@router.post("/reload")
async def reload_mcp_servers(
    current_user: Annotated[UserRead, Depends(require_admin)],
):
    """重新连接所有启用的 MCP Server 并注册工具"""
    await load_mcp_tools()
    return {"status": "reloaded"}


@router.get("/status", response_model=list[MCPServerStatus])
async def mcp_status(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """获取 MCP Server 连接状态"""
    return await get_mcp_status()
