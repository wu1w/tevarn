"""
MCP 工具适配器

将 MCP Server 返回的工具定义包装成 BaseTool，接入统一 ToolRegistry。
"""

from __future__ import annotations

from typing import Any

from backend.tools.base import BaseTool, ToolRiskLevel, ToolSource
from backend.mcp_hub.client import MCPClient, MCPClientManager


class MCPToolAdapter(BaseTool):
    """单个 MCP 工具的 BaseTool 包装"""

    def __init__(
        self,
        server_name: str,
        tool_name: str,
        description: str,
        parameters: dict[str, Any],
        client: MCPClient | None = None,
        client_manager: MCPClientManager | None = None,
    ):
        self.server_name = server_name
        self._client = client
        self._client_manager = client_manager

        # 自动加 mcp_ 前缀避免与内置工具冲突
        prefixed_name = f"mcp_{tool_name}" if not tool_name.startswith("mcp_") else tool_name

        # MCP 工具默认中风险；未来可通过服务器元数据或配置覆盖
        super().__init__(
            name=prefixed_name,
            description=description,
            parameters=parameters,
            source=ToolSource.MCP,
            risk_level=ToolRiskLevel.MEDIUM,
            enabled=True,
        )

    def _get_client(self) -> MCPClient | None:
        if self._client is not None:
            return self._client
        if self._client_manager is None:
            from backend.mcp_hub.client import get_mcp_manager

            self._client_manager = get_mcp_manager()
        return self._client_manager.get_client(self.server_name)

    async def execute(self, **kwargs) -> Any:
        client = self._get_client()
        if client is None:
            return f"[Error] MCP server '{self.server_name}' not connected"
        # Strip mcp_ prefix when calling the remote server — the prefix is
        # only for local disambiguation in ToolRegistry; the MCP server only
        # knows its own tool names.
        remote_name = self.name
        if remote_name.startswith("mcp_"):
            remote_name = remote_name[4:]
        # Agent loop 会注入 _session_id / _ws_manager / user_id；
        # MCP JSON-RPC 无法序列化 ConnectionManager 等对象，必须剥离。
        clean = {
            k: v
            for k, v in kwargs.items()
            if not str(k).startswith("_")
            and str(k) not in ("ws_manager", "connection_manager", "user_id")
            and "ConnectionManager" not in type(v).__name__
        }
        return await client.call_tool(remote_name, clean)


async def register_mcp_server_tools(
    server_name: str,
    client: MCPClient,
    registry=None,
) -> int:
    """从 MCP Server 拉取工具列表并注册到统一 ToolRegistry"""
    from backend.tools.registry import ToolRegistry

    target_registry = registry or ToolRegistry

    tools = await client.list_tools()
    for tool in tools.tools:
        adapter = MCPToolAdapter(
            server_name=server_name,
            tool_name=tool.name,
            description=tool.description or "",
            parameters=tool.inputSchema or {"type": "object", "properties": {}},
            client=client,
        )
        target_registry.register(adapter)
    return len(tools.tools)
