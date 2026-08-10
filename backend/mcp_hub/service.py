"""
MCP 服务模块

对齐 Hermes `/reload-mcp` 与 OpenClaw `mcp reload`：
- 按 server 安全重连（先连新后换旧）
- 仅对成功连接的 server 注册工具
- 禁用/删除的 server 卸工具并断开
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.mcp_hub.client import MCPClientManager, MCPServerConfig
from backend.mcp_hub.normalize import merge_env, normalize_stdio_command_args
from backend.repositories.mcp_server_repo import AsyncMCPServerRepository
from backend.tools.adapters.mcp_adapter import (
    register_mcp_server_tools,
    unregister_all_mcp_tools,
    unregister_mcp_server_tools,
)

logger = logging.getLogger(__name__)

_sync_lock = asyncio.Lock()


def _row_to_config(s: Any) -> MCPServerConfig:
    cmd, args = normalize_stdio_command_args(
        str(getattr(s, "name", "") or ""),
        getattr(s, "command", None),
        list(getattr(s, "args", None) or []),
    )
    env = merge_env(None, getattr(s, "env", None) or {})
    return MCPServerConfig(
        name=s.name,
        transport=s.transport,
        command=cmd,
        args=args,
        url=s.url,
        env=env,
        enabled=bool(s.enabled),
        timeout=float(getattr(s, "timeout", 30.0) or 30.0),
    )


async def unregister_mcp_tools_from_registry() -> int:
    return unregister_all_mcp_tools()


async def load_mcp_tools(manager: MCPClientManager | None = None) -> MCPClientManager:
    """兼容旧调用：全量同步。"""
    await sync_mcp_runtime(manager)
    if manager is None:
        from backend.mcp_hub.client import get_mcp_manager

        return get_mcp_manager()
    return manager


async def sync_mcp_runtime(
    manager: MCPClientManager | None = None,
    *,
    only_server: str | None = None,
) -> dict[str, Any]:
    """DB enabled 集合 → 运行时连接 + ToolRegistry。

    - only_server: 只重连指定 server（manage_mcp update 用）
    - 非破坏：reconnect 失败时保留旧连接与旧工具
    """
    async with _sync_lock:
        from backend.mcp_hub.client import get_mcp_manager

        if manager is None:
            manager = get_mcp_manager()

        repo = AsyncMCPServerRepository()
        try:
            all_rows = await repo.list_all()
        except Exception:
            all_rows = await repo.get_all_enabled()

        enabled_rows = [s for s in all_rows if getattr(s, "enabled", False)]
        if only_server:
            enabled_rows = [s for s in enabled_rows if s.name == only_server]
            # 若 only 指向已禁用 server，仍要断开
            if not enabled_rows:
                await manager.disconnect(only_server)
                n = unregister_mcp_server_tools(only_server)
                return {
                    "ok": True,
                    "unregistered": n,
                    "connected": manager.list_connected(),
                    "error": None,
                    "warning": f"server_disabled_or_missing:{only_server}",
                }

        enabled_names = {s.name for s in enabled_rows}
        risk_by_server = {
            s.name: str(getattr(s, "risk_level", None) or "low") for s in enabled_rows
        }

        # 断开已禁用且不在 only 范围外的？
        if only_server is None:
            for name in list(manager.list_connected()):
                if name not in enabled_names:
                    await manager.disconnect(name)
                    unregister_mcp_server_tools(name)

        connected: list[str] = []
        failed: list[str] = []
        registered_total = 0

        for s in enabled_rows:
            cfg = _row_to_config(s)
            client = await manager.reconnect(cfg)
            if client is None or not getattr(client, "_initialized", False):
                failed.append(s.name)
                # 连接失败：卸该 server 旧工具，避免僵尸
                unregister_mcp_server_tools(s.name)
                continue
            # 成功：先卸该 server 旧工具再注册
            unregister_mcp_server_tools(s.name)
            try:
                inc = getattr(s, "tools_include", None)
                exc = getattr(s, "tools_exclude", None)
                count = await register_mcp_server_tools(
                    s.name,
                    client,
                    risk_level=risk_by_server.get(s.name, "low"),
                    tools_include=list(inc) if inc else None,
                    tools_exclude=list(exc) if exc else None,
                )
                registered_total += count
                connected.append(s.name)
                logger.info(
                    "Registered %s MCP tools from server '%s' (include=%s exclude=%s)",
                    count,
                    s.name,
                    inc,
                    exc,
                )
            except Exception as e:
                logger.warning(
                    "Failed to register MCP tools from '%s': %s", s.name, e
                )
                failed.append(s.name)

        warning = None
        if failed:
            warning = f"connect_failed:{','.join(failed)}"
            logger.warning("sync_mcp_runtime partial: %s", warning)

        logger.info(
            "sync_mcp_runtime: connected=%s registered=%s warning=%s",
            connected,
            registered_total,
            warning,
        )
        return {
            "ok": len(failed) == 0 or len(connected) > 0,
            "unregistered": 0,
            "connected": connected,
            "registered": registered_total,
            "error": None if connected or not failed else f"all_failed:{failed}",
            "warning": warning,
            "enabled_count": len(enabled_rows),
            "failed": failed,
        }


async def get_mcp_status() -> list[dict]:
    """enabled vs connected 分离；tool_count 优先 Registry/缓存。"""
    from backend.mcp_hub.client import get_mcp_manager
    from backend.tools.base import ToolSource
    from backend.tools.registry import ToolRegistry

    manager = get_mcp_manager()
    repo = AsyncMCPServerRepository()
    try:
        servers = await repo.list_all()
    except Exception:
        servers = await repo.get_all_enabled()

    reg_count: dict[str, int] = {}
    try:
        for t in ToolRegistry.get_all(source=ToolSource.MCP):
            sn = str(getattr(t, "server_name", "") or "")
            if sn:
                reg_count[sn] = reg_count.get(sn, 0) + 1
    except Exception:
        pass

    status = []
    for s in servers:
        client = manager.get_client(s.name)
        live = client is not None and bool(getattr(client, "_initialized", False))
        connected = bool(getattr(s, "enabled", False)) and live
        tool_count = reg_count.get(s.name, 0)
        if live and tool_count <= 0:
            tool_count = int(getattr(client, "_cached_tool_count", 0) or 0)
        error = None
        if not getattr(s, "enabled", False):
            connected = False
        elif getattr(s, "enabled", False) and not connected:
            error = "not connected"
        status.append(
            {
                "name": s.name,
                "transport": s.transport,
                "enabled": bool(getattr(s, "enabled", False)),
                "connected": connected,
                "tool_count": tool_count,
                "error": error,
            }
        )
    return status
