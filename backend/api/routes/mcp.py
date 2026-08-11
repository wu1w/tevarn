"""
MCP Server 管理 API
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.dependencies import get_current_user, require_admin
from backend.mcp_hub.service import get_mcp_status, sync_mcp_runtime
from backend.repositories.mcp_server_repo import AsyncMCPServerRepository
from backend.schemas.mcp import (
    MCPRemoteToolInfo,
    MCPServerConfig,
    MCPServerCreate,
    MCPServerStatus,
    MCPServerToggle,
    MCPServerToolsPolicy,
    MCPServerUpdate,
)
from backend.schemas.user import UserRead

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp", tags=["MCP"])


_mcp_repo = AsyncMCPServerRepository()


def get_mcp_server_repo():
    return _mcp_repo


async def _hot_reload(only_server: str | None = None) -> dict:
    """配置变更后热同步 MCP 连接与 ToolRegistry。

    only_server 非空时只重连该服，避免全量 close_all 卸光其它 MCP。
    """
    try:
        result = await sync_mcp_runtime(only_server=only_server)
        if not result.get("ok"):
            logger.warning("MCP hot reload incomplete: %s", result.get("error"))
        elif result.get("warning"):
            logger.warning("MCP hot reload warning: %s", result.get("warning"))
        return result
    except Exception as e:
        logger.exception("MCP hot reload failed: %s", e)
        return {"ok": False, "error": str(e), "connected": [], "unregistered": 0}


def _public_mcp_config(row: object, *, reveal_env: bool = False) -> MCPServerConfig:
    """API 响应：默认脱敏 env 值，只暴露 env_keys。"""
    cfg = MCPServerConfig.model_validate(row)
    raw_env = dict(cfg.env or {})
    keys = sorted(str(k) for k in raw_env.keys())
    if reveal_env:
        return cfg.model_copy(update={"env_keys": keys})
    redacted = {
        str(k): ("***" if str(v or "").strip() else "")
        for k, v in raw_env.items()
    }
    return cfg.model_copy(update={"env": redacted, "env_keys": keys})


def _with_runtime(cfg: MCPServerConfig, runtime: dict, *, name: str | None = None) -> MCPServerConfig:
    """把热同步结果附到 MCPServerConfig（列表接口不带这些字段）。"""
    sname = name or cfg.name
    connected_list = list(runtime.get("connected") or [])
    connected = sname in connected_list
    ok = bool(runtime.get("ok")) and connected
    err = None
    if not ok:
        err = str(
            runtime.get("connect_error")
            or runtime.get("error")
            or runtime.get("warning")
            or "not connected"
        )[:500]
    return cfg.model_copy(
        update={
            "runtime_ok": ok,
            "runtime_connected": connected,
            "runtime_error": err,
            "runtime_conclude": not ok,
            "runtime_registered": int(runtime.get("registered") or 0),
        }
    )


@router.get("", response_model=list[MCPServerConfig])
async def list_mcp_servers(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[AsyncMCPServerRepository, Depends(get_mcp_server_repo)],
):
    """列出所有 MCP Server 配置（env 值脱敏）。"""
    rows = await repo.list_all()
    return [_public_mcp_config(r) for r in rows]


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
        from backend.mcp_hub.normalize import normalize_server_fields

        dump = data.model_dump(exclude={"name"})
        norm = normalize_server_fields(
            name=data.name,
            command=dump.get("command"),
            args=list(dump.get("args") or []),
            env=dump.get("env") or {},
            existing_env=getattr(existing, "env", None) or {},
        )
        dump["command"] = norm["command"]
        dump["args"] = norm["args"]
        dump["env"] = norm["env"]
        upd = MCPServerUpdate(**dump)
        updated = await repo.update(existing.id, upd)
        rt = await _hot_reload(only_server=data.name)
        cfg = _public_mcp_config(updated)
        if not rt.get("ok"):
            logger.error("MCP create/upsert runtime sync failed: %s", rt.get("error"))
        return _with_runtime(cfg, rt, name=data.name)
    # 规范化 command/args/env（纠正 tavily 错包等）
    from backend.mcp_hub.normalize import normalize_server_fields

    norm = normalize_server_fields(
        name=data.name,
        command=data.command,
        args=list(data.args or []),
        env=data.env or {},
        existing_env={},
    )
    data = data.model_copy(
        update={
            "command": norm["command"],
            "args": norm["args"],
            "env": norm["env"],
        }
    )
    server = await repo.create(data)
    rt = await _hot_reload(only_server=data.name)
    if not rt.get("ok"):
        logger.error("MCP create runtime sync failed: %s", rt.get("error"))
    return _with_runtime(_public_mcp_config(server), rt, name=data.name)


@router.put("/{server_id}", response_model=MCPServerConfig)
async def update_mcp_server(
    server_id: uuid.UUID,
    data: MCPServerUpdate,
    current_user: Annotated[UserRead, Depends(require_admin)],
    repo: Annotated[AsyncMCPServerRepository, Depends(get_mcp_server_repo)],
):
    """更新 MCP Server（仅管理员），成功后热重连。"""
    from backend.mcp_hub.normalize import normalize_server_fields

    existing = await repo.get_by_id(server_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="MCP server not found")
    dump = data.model_dump(exclude_unset=True)
    name = str(dump.get("name") or existing.name)
    norm = normalize_server_fields(
        name=name,
        command=dump.get("command", existing.command),
        args=dump.get("args", existing.args or []),
        env=dump.get("env") if "env" in dump else {},
        existing_env=existing.env or {},
    )
    if any(k in dump for k in ("command", "args", "env", "name")) or name.lower() in (
        "tavily",
        "firecrawl",
        "doubao-search",
        "doubao",
        "askecho",
        "askecho-search",
        "mcp-server-askecho-search-infinity",
    ):
        dump["command"] = norm["command"]
        dump["args"] = norm["args"]
        dump["env"] = norm["env"]
    updated = await repo.update(server_id, MCPServerUpdate(**dump))
    if updated is None:
        raise HTTPException(status_code=404, detail="MCP server not found")
    rt = await _hot_reload(only_server=name)
    if not rt.get("ok"):
        logger.error("MCP update runtime sync failed: %s", rt.get("error"))
    return _with_runtime(_public_mcp_config(updated), rt, name=name)


@router.put("/{server_id}/toggle", response_model=MCPServerConfig)
async def toggle_mcp_server(
    server_id: uuid.UUID,
    data: MCPServerToggle,
    current_user: Annotated[UserRead, Depends(require_admin)],
    repo: Annotated[AsyncMCPServerRepository, Depends(get_mcp_server_repo)],
):
    """切换启用状态，并热同步连接/工具注册表。"""
    server = await repo.toggle(server_id, data.enabled)
    if server is None:
        raise HTTPException(status_code=404, detail="MCP server not found")
    await _hot_reload(only_server=str(getattr(server, "name", "") or ""))
    return _public_mcp_config(server)


@router.delete("/{server_id}")
async def delete_mcp_server(
    server_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(require_admin)],
    repo: Annotated[AsyncMCPServerRepository, Depends(get_mcp_server_repo)],
):
    """删除 MCP Server 并热同步（断开连接 + 卸载工具）。"""
    existing = await repo.get_by_id(server_id)
    sname = str(getattr(existing, "name", "") or "") if existing else ""
    success = await repo.delete(server_id)
    if not success:
        raise HTTPException(status_code=404, detail="MCP server not found")
    await _hot_reload(only_server=sname or None)
    return {"deleted": True}


@router.post("/reload")
async def reload_mcp_servers(
    current_user: Annotated[UserRead, Depends(require_admin)],
):
    """重新连接所有启用的 MCP Server 并注册工具"""
    result = await _hot_reload()
    return {
        "status": "reloaded" if result.get("ok") else "error",
        "connected": result.get("connected") or [],
        "unregistered": result.get("unregistered", 0),
        "error": result.get("error"),
    }


@router.get("/status", response_model=list[MCPServerStatus])
async def mcp_status(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """获取 MCP Server 连接状态（含 enabled/connected 分离字段）"""
    return await get_mcp_status()


@router.get("/{server_id}/tools", response_model=list[MCPRemoteToolInfo])
async def list_mcp_server_tools(
    server_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[AsyncMCPServerRepository, Depends(get_mcp_server_repo)],
):
    """列出远端 MCP 工具 + 当前白名单选中状态（Hermes 安装后 checklist）。"""
    from backend.mcp_hub.client import get_mcp_manager
    from backend.mcp_hub.normalize import tool_name_allowed
    from backend.mcp_hub.service import sync_mcp_runtime
    from backend.tools.adapters.mcp_adapter import mcp_registry_name

    server = await repo.get_by_id(server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="MCP server not found")
    manager = get_mcp_manager()
    client = manager.get_client(server.name)
    if client is None or not getattr(client, "_initialized", False):
        if server.enabled:
            await sync_mcp_runtime(only_server=server.name)
            client = manager.get_client(server.name)

    include = getattr(server, "tools_include", None)
    exclude = getattr(server, "tools_exclude", None)
    out: list[MCPRemoteToolInfo] = []

    # 优先 RPC list_tools；失败则回退到 Registry 已挂载项 + 缓存
    remote_tools: list[tuple[str, str]] = []
    if client is not None and getattr(client, "_initialized", False):
        try:
            remote = await client.list_tools()
            remote_tools = [
                (t.name, (t.description or "")[:500]) for t in remote.tools
            ]
            # 缓存原始清单供 status/list 降级
            try:
                client._cached_remote_tools = [
                    {"name": n, "description": d} for n, d in remote_tools
                ]
            except Exception:
                pass
        except Exception as e:
            logger.warning("list_tools RPC failed for %s: %s", server.name, e)

    if not remote_tools:
        cached = getattr(client, "_cached_remote_tools", None) if client else None
        if cached:
            remote_tools = [
                (str(x.get("name") or ""), str(x.get("description") or "")[:500])
                for x in cached
                if x.get("name")
            ]
        else:
            from backend.tools.base import ToolSource
            from backend.tools.registry import ToolRegistry

            for t in ToolRegistry.get_all(source=ToolSource.MCP):
                if getattr(t, "server_name", None) != server.name:
                    continue
                remote_tools.append(
                    (
                        str(getattr(t, "remote_name", None) or t.name),
                        (t.description or "")[:500],
                    )
                )

    if not remote_tools and (
        client is None or not getattr(client, "_initialized", False)
    ):
        raise HTTPException(
            status_code=503,
            detail=f"MCP server '{server.name}' not connected",
        )

    for rname, desc in remote_tools:
        selected = tool_name_allowed(rname, include, exclude)
        out.append(
            MCPRemoteToolInfo(
                name=rname,
                description=desc,
                selected=selected,
                registry_name=mcp_registry_name(server.name, rname),
            )
        )
    return out


@router.put("/{server_id}/tools", response_model=MCPServerConfig)
async def set_mcp_server_tools(
    server_id: uuid.UUID,
    data: MCPServerToolsPolicy,
    current_user: Annotated[UserRead, Depends(require_admin)],
    repo: Annotated[AsyncMCPServerRepository, Depends(get_mcp_server_repo)],
):
    """设置 tools.include / tools.exclude 并热同步（仅重连该 server）。"""
    from backend.mcp_hub.normalize import normalize_tool_name_list
    from backend.mcp_hub.service import sync_mcp_runtime

    server = await repo.get_by_id(server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="MCP server not found")

    patch: dict = {}
    # 区分「未传」与「显式清空」：用 model_fields_set
    fields = getattr(data, "model_fields_set", None) or getattr(data, "__fields_set__", set())
    if "tools_include" in fields:
        patch["tools_include"] = normalize_tool_name_list(data.tools_include)
    if "tools_exclude" in fields:
        patch["tools_exclude"] = normalize_tool_name_list(data.tools_exclude)
    if not patch:
        raise HTTPException(status_code=400, detail="provide tools_include and/or tools_exclude")

    updated = await repo.update(server_id, MCPServerUpdate(**patch))
    if updated is None:
        raise HTTPException(status_code=404, detail="MCP server not found")
    try:
        await sync_mcp_runtime(only_server=updated.name)
    except Exception as e:
        logger.warning("tools policy sync failed: %s", e)
    return _public_mcp_config(updated)
