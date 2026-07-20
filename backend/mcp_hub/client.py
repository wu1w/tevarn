"""
MCP 客户端基础封装

使用官方 modelcontextprotocol/python-sdk：
- stdio_client: 启动本地命令作为 MCP server
- sse_client: 连接远程 SSE endpoint
- ClientSession: list_tools / call_tool

所有 server 连接通过 `MCPClient` 统一管理，
支持多个 server 同时连接，并在应用关闭时统一清理。
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, ListToolsResult, TextContent

logger = logging.getLogger(__name__)


@dataclass
class MCPServerConfig:
    """单个 MCP Server 配置"""

    name: str
    transport: str  # "stdio" | "sse"
    command: str | None = None  # stdio: 可执行命令
    args: list[str] | None = None  # stdio: 命令参数
    url: str | None = None  # sse: endpoint URL
    env: dict[str, str] | None = None
    enabled: bool = True
    timeout: float = 30.0


class MCPClient:
    """MCP 客户端连接管理器"""

    def __init__(self, config: MCPServerConfig):
        self.config = config
        self.name = config.name
        self._session: ClientSession | None = None
        self._exit_stack = AsyncExitStack()
        self._initialized = False

    async def connect(self) -> None:
        """建立到 MCP Server 的连接并初始化 session"""
        if not self.config.enabled:
            raise RuntimeError(f"MCP server '{self.name}' is disabled")

        if self.config.transport == "stdio":
            await self._connect_stdio()
        elif self.config.transport == "sse":
            await self._connect_sse()
        else:
            raise ValueError(f"Unsupported transport: {self.config.transport}")

        self._initialized = True
        logger.info(f"MCP server '{self.name}' connected")

    async def _connect_stdio(self) -> None:
        if not self.config.command:
            raise ValueError(f"MCP server '{self.name}' stdio transport requires command")

        server_params = StdioServerParameters(
            command=self.config.command,
            args=self.config.args or [],
            env=self.config.env,
        )
        read, write = await self._exit_stack.enter_async_context(
            stdio_client(server_params)
        )
        session = await self._exit_stack.enter_async_context(
            ClientSession(read, write)
        )
        await session.initialize()
        self._session = session

    async def _connect_sse(self) -> None:
        if not self.config.url:
            raise ValueError(f"MCP server '{self.name}' sse transport requires url")

        read, write = await self._exit_stack.enter_async_context(
            sse_client(self.config.url)
        )
        session = await self._exit_stack.enter_async_context(
            ClientSession(read, write)
        )
        await session.initialize()
        self._session = session

    async def list_tools(self) -> ListToolsResult:
        """列出 server 提供的所有工具"""
        if self._session is None or not self._initialized:
            raise RuntimeError(f"MCP server '{self.name}' not connected")
        return await self._session.list_tools()

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """调用 server 上的工具"""
        if self._session is None or not self._initialized:
            raise RuntimeError(f"MCP server '{self.name}' not connected")

        # 防御：剥除不可 JSON 的内部注入字段（_ws_manager / ConnectionManager）
        import json as _json

        safe_args: dict[str, Any] = {}
        if isinstance(arguments, dict):
            for k, v in arguments.items():
                ks = str(k)
                if ks.startswith("_") or ks in ("ws_manager", "connection_manager", "user_id"):
                    continue
                if "ConnectionManager" in type(v).__name__:
                    continue
                try:
                    safe_args[ks] = _json.loads(_json.dumps(v, default=str))
                except Exception:
                    safe_args[ks] = str(v)

        result: CallToolResult = await self._session.call_tool(tool_name, safe_args)

        # 将结果统一转为字符串
        parts: list[str] = []
        for content in result.content:
            if isinstance(content, TextContent):
                parts.append(content.text)
            else:
                parts.append(str(content))
        return chr(10).join(parts)

    async def close(self) -> None:
        """关闭连接并清理资源"""
        await self._exit_stack.aclose()
        self._session = None
        self._initialized = False
        logger.info(f"MCP server '{self.name}' disconnected")

    async def __aenter__(self) -> MCPClient:
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()


class MCPClientManager:
    """管理多个 MCPClient 实例"""

    def __init__(self):
        self._clients: dict[str, MCPClient] = {}

    async def connect(self, configs: list[MCPServerConfig]) -> None:
        """批量连接多个 server"""
        for config in configs:
            if not config.enabled:
                continue
            client = MCPClient(config)
            try:
                await client.connect()
                self._clients[config.name] = client
            except Exception as e:
                logger.warning(f"Failed to connect MCP server '{config.name}': {e}")
                await client.close()

    def get_client(self, server_name: str) -> MCPClient | None:
        return self._clients.get(server_name)

    def list_connected(self) -> list[str]:
        return list(self._clients.keys())

    async def close_all(self) -> None:
        await asyncio.gather(
            *[client.close() for client in self._clients.values()],
            return_exceptions=True,
        )
        self._clients.clear()

    async def __aenter__(self) -> MCPClientManager:
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close_all()


# 全局 MCP 管理器（单例）
_mcp_manager: MCPClientManager | None = None


def get_mcp_manager() -> MCPClientManager:
    """获取全局 MCPClientManager 单例"""
    global _mcp_manager
    if _mcp_manager is None:
        _mcp_manager = MCPClientManager()
    return _mcp_manager


def reset_mcp_manager() -> None:
    """重置全局单例（测试用）"""
    global _mcp_manager
    _mcp_manager = None
