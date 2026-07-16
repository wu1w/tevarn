"""
MCP 服务模块

- 从数据库读取 MCP Server 配置
- 连接所有启用 server
- 将工具注册到统一 ToolRegistry
- 提供重新加载和状态查询
"""

from __future__ import annotations

import logging

from backend.mcp_hub.client import MCPClientManager, MCPServerConfig
from backend.repositories.mcp_server_repo import AsyncMCPServerRepository
from backend.tools.adapters.mcp_adapter import register_mcp_server_tools

logger = logging.getLogger(__name__)


async def load_mcp_tools(manager: MCPClientManager | None = None) -> MCPClientManager:
    """从数据库加载所有启用的 MCP Server，连接并注册其工具"""
    if manager is None:
        from backend.mcp_hub.client import get_mcp_manager

        manager = get_mcp_manager()

    # 先关闭已有连接
    await manager.close_all()

    repo = AsyncMCPServerRepository()
    servers = await repo.get_all_enabled()

    configs = []
    for s in servers:
        configs.append(
            MCPServerConfig(
                name=s.name,
                transport=s.transport,
                command=s.command,
                args=s.args or [],
                url=s.url,
                env=s.env or {},
                enabled=s.enabled,
                timeout=s.timeout,
            )
        )

    await manager.connect(configs)

    for name in manager.list_connected():
        client = manager.get_client(name)
        if client is not None:
            try:
                count = await register_mcp_server_tools(name, client)
                logger.info(f"Registered {count} MCP tools from server '{name}'")
            except Exception as e:
                logger.warning(f"Failed to register MCP tools from '{name}': {e}")

    return manager


async def get_mcp_status() -> list[dict]:
    """获取所有 MCP Server 的连接状态"""
    from backend.mcp_hub.client import get_mcp_manager

    manager = get_mcp_manager()
    repo = AsyncMCPServerRepository()
    servers = await repo.get_all_enabled()

    status = []
    for s in servers:
        client = manager.get_client(s.name)
        connected = client is not None and client._initialized
        tool_count = 0
        error = None
        if client is not None and connected:
            try:
                tools = await client.list_tools()
                tool_count = len(tools.tools)
            except Exception as e:
                error = str(e)
                connected = False
        status.append(
            {
                "name": s.name,
                "transport": s.transport,
                "connected": connected,
                "tool_count": tool_count,
                "error": error,
            }
        )
    return status
