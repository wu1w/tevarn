"""
MCP 商店 API：目录浏览 + 一键安装（写入 /mcp 配置）

对标 skill_store：多源聚合、失败降级、统一元数据。
生态说明：Claude Code / Hermes / OpenClaw / Codex 使用同一 MCP 协议，
公共池为 Official Registry；安装配置（npx/uvx/SSE URL）可互通。
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.api.dependencies import get_current_user
from backend.repositories.mcp_server_repo import AsyncMCPServerRepository
from backend.schemas.mcp import MCPServerCreate
from backend.schemas.mcp_store import MCPStoreListResponse, MCPStoreSourceInfo, UnifiedMCP
from backend.schemas.user import UserRead
from backend.services.mcp_store import get_mcp_store_service

router = APIRouter(prefix="/mcp/store", tags=["MCP Store"])
_repo = AsyncMCPServerRepository()


class MCPStoreInstallRequest(BaseModel):
    source: str = Field(..., description="curated | official")
    id: str = Field(..., description="商店条目 id")


class MCPStoreInstallResponse(BaseModel):
    success: bool
    server_id: str | None = None
    server_name: str | None = None
    message: str = ""
    need_env: list[str] = Field(default_factory=list)


def _server_id_name(server: Any) -> tuple[str | None, str | None]:
    if server is None:
        return None, None
    sid = getattr(server, "id", None)
    sname = getattr(server, "name", None)
    return (str(sid) if sid is not None else None, str(sname) if sname else None)


@router.get("/sources", response_model=list[MCPStoreSourceInfo])
async def list_mcp_store_sources(
    current_user: Annotated[UserRead, Depends(get_current_user)] = None,
):
    svc = get_mcp_store_service()
    return await svc.list_sources()


@router.get("/list", response_model=MCPStoreListResponse)
async def list_mcp_store(
    source: str | None = Query(None, description="curated|official|all"),
    search: str = Query("", description="关键词"),
    limit: int = Query(48, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: Annotated[UserRead, Depends(get_current_user)] = None,
):
    svc = get_mcp_store_service()
    return await svc.list_items(source=source, search=search, limit=limit, offset=offset)


@router.get("/{source}/{item_id}", response_model=UnifiedMCP)
async def get_mcp_store_item(
    source: str,
    item_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)] = None,
):
    svc = get_mcp_store_service()
    item = await svc.resolve_item(source, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="MCP store item not found")
    return item


@router.post("/install", response_model=MCPStoreInstallResponse)
async def install_mcp_from_store(
    body: MCPStoreInstallRequest,
    current_user: Annotated[UserRead, Depends(get_current_user)] = None,
):
    """一键安装：目录项 → Takton MCP Server 配置。"""
    svc = get_mcp_store_service()
    item = await svc.resolve_item(body.source, body.id)
    if not item:
        raise HTTPException(status_code=404, detail="MCP store item not found")
    if not item.installable:
        raise HTTPException(
            status_code=400,
            detail=item.note or "This MCP cannot be one-click installed; use custom form",
        )

    existing = await _repo.get_by_name(item.name)
    if existing:
        sid, sname = _server_id_name(existing)
        return MCPStoreInstallResponse(
            success=False,
            server_id=sid,
            server_name=sname,
            message=f"已存在同名 MCP「{item.name}」",
        )

    need_env: list[str] = []
    env: dict[str, str] = {}
    if item.env_hint:
        for line in item.env_hint.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, _, v = line.partition("=")
                k = k.strip()
                if k:
                    env[k] = v.strip()
                    if not v.strip():
                        need_env.append(k)

    create = MCPServerCreate(
        name=item.name[:64],
        transport=item.transport,
        command=item.command or None,
        args=item.args or [],
        url=item.url or None,
        env=env or {},
        enabled=True,
        timeout=30.0,
        risk_level=item.risk_level
        if item.risk_level in ("safe", "low", "medium", "high", "dangerous")
        else "medium",
        allowed_paths=None,
    )
    server = await _repo.create(create)
    sid, sname = _server_id_name(server)

    msg = f"已安装 {item.display_name}"
    if need_env:
        msg += f" · 请到「已安装/编辑」填写环境变量: {', '.join(need_env)}"
    if item.note:
        msg += f" · {item.note}"
    return MCPStoreInstallResponse(
        success=True,
        server_id=sid,
        server_name=sname,
        message=msg,
        need_env=need_env,
    )
