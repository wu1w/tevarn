"""
MCP 商店 API：目录浏览 + 一键安装（写入 /mcp 配置）

对标 skill_store：多源聚合、失败降级、统一元数据。
生态说明：Claude Code / Hermes / OpenClaw / Codex 使用同一 MCP 协议，
公共池为 Official Registry；安装配置（npx/uvx/SSE URL）可互通。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.api.dependencies import get_current_user
from backend.repositories.mcp_server_repo import AsyncMCPServerRepository
from backend.schemas.mcp import MCPServerCreate
from backend.schemas.mcp_store import (
    MCPStoreListResponse,
    MCPStoreSourceInfo,
    UnifiedMCP,
)
from backend.schemas.user import UserRead
from backend.services.mcp_store import get_mcp_store_service

router = APIRouter(prefix="/mcp/store", tags=["MCP Store"])
_repo = AsyncMCPServerRepository()


class MCPStoreInstallRequest(BaseModel):
    source: str = Field(..., description="curated | official")
    id: str = Field(..., description="商店条目 id")
    # 安装时可选工具白名单（原始 MCP 工具名）；空=全量挂载，之后可用 PUT /mcp/{id}/tools 调整
    tools_include: list[str] | None = Field(
        default=None,
        description="可选：仅挂载这些工具（Hermes tools.include）；省略则挂全部",
    )
    tools_exclude: list[str] | None = Field(
        default=None,
        description="可选：排除工具（仅当 tools_include 为空时生效）",
    )


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
    """一键安装：目录项 → Tevarn MCP Server 配置。"""
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

    from backend.mcp_hub.normalize import (
        normalize_server_fields,
        normalize_tool_name_list,
    )

    norm = normalize_server_fields(
        name=item.name[:64],
        command=item.command or None,
        args=list(item.args or []),
        env=env or {},
        existing_env={},
    )
    # 安装时不写入空密钥，避免子进程被空 Key 覆盖
    clean_env = {k: v for k, v in (norm["env"] or {}).items() if str(v).strip()}
    from backend.mcp_hub.normalize import is_url_transport, normalize_transport

    transport = normalize_transport(item.transport) or "stdio"
    # 远程 / mcp-remote 冷启动：给更宽默认超时
    install_timeout = 30.0
    low_name = (item.name or "").lower()
    joined_args = " ".join(str(a) for a in (item.args or [])).lower()
    if is_url_transport(transport) or "mcp-remote" in joined_args or low_name in (
        "deepwiki",
        "mcp-remote",
    ):
        install_timeout = 120.0 if "mcp-remote" in joined_args else 60.0

    create = MCPServerCreate(
        name=item.name[:64],
        transport=transport,
        command=norm["command"] if transport == "stdio" else (item.command or None),
        args=norm["args"] if transport == "stdio" else list(item.args or []),
        url=item.url or None,
        env=clean_env,
        enabled=True,
        timeout=install_timeout,
        risk_level=item.risk_level
        if item.risk_level in ("safe", "low", "medium", "high", "dangerous")
        else "low",
        allowed_paths=None,
        tools_include=normalize_tool_name_list(body.tools_include),
        tools_exclude=normalize_tool_name_list(body.tools_exclude),
    )
    server = await _repo.create(create)
    sid, sname = _server_id_name(server)

    # 安装后热挂载：enabled 服务器立即连接并注册工具
    runtime_ok = True
    runtime_err = ""
    registered = 0
    try:
        from backend.mcp_hub.service import sync_mcp_runtime

        rt = await sync_mcp_runtime(only_server=sname)
        runtime_ok = bool(rt.get("ok")) and sname in (rt.get("connected") or [])
        runtime_err = str(
            rt.get("connect_error") or rt.get("error") or rt.get("warning") or ""
        )
        registered = int(rt.get("registered") or 0)
    except Exception as e:
        runtime_ok = False
        runtime_err = str(e)

    msg = f"已安装 {item.display_name}"
    if create.tools_include:
        msg += f" · 白名单 {len(create.tools_include)} 个工具"
    if need_env:
        msg += f" · 请到「已安装/编辑」填写环境变量: {', '.join(need_env)}"
    if item.note:
        msg += f" · {item.note}"
    if runtime_ok:
        msg += f" · 已挂载 {registered} 个工具（可用 PUT /api/mcp/{{id}}/tools 调整白名单）"
    else:
        # 安装失败自动收束：DB 已写，但勿诱导 UI/agent 立刻连环重装
        msg += (
            f" · 运行时挂载失败: {runtime_err or 'unknown'}。"
            f"【自动收束】配置已保存；请按失败原因处理（缺命令/网络/URL/timeout），再点「重新加载 MCP」，"
            f"勿反复卸载重装同一 server。"
        )
    return MCPStoreInstallResponse(
        success=runtime_ok,  # 连接失败时 success=false，便于前端/agent 收束
        server_id=sid,
        server_name=sname,
        message=msg,
        need_env=need_env,
    )
