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
import os
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client

# audit-fix: stdio monkeypatch 捕获 proc 的并发互斥锁
_STDIO_PATCH_LOCK = asyncio.Lock()
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
        # audit-fix: stdio 子进程句柄（aclose 跨 task cancel scope 失败时兜底 terminate）
        self._proc: Any | None = None

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

        # Windows / Electron 精简 PATH 下裸 npx、uvx 常失败 → 解析绝对路径并补全 PATH
        from backend.core.host_commands import build_process_env, resolve_host_command

        raw_cmd = str(self.config.command or "").strip()
        resolved_cmd = resolve_host_command(raw_cmd)
        if resolved_cmd != raw_cmd:
            logger.info(
                "MCP server '%s' command resolved %r → %r",
                self.name,
                raw_cmd,
                resolved_cmd,
            )
        # 合并：宿主 PATH 补全 + 用户 env（API Key 等）；PATH 以补全版为准
        child_env = build_process_env(self.config.env)
        if resolved_cmd.lower().endswith((".cmd", ".bat")) and "PATHEXT" not in child_env:
            child_env["PATHEXT"] = os.environ.get(
                "PATHEXT", ".COM;.EXE;.BAT;.CMD;.VBS;.JS;.WS;.MSC"
            )

        server_params = StdioServerParameters(
            command=resolved_cmd,
            args=self.config.args or [],
            env=child_env,
        )
        # audit-fix: stdio_client 不暴露子进程句柄；在 enter 期间临时包裹
        # SDK 内部的 _create_platform_compatible_process 捕获 proc，
        # 供 close() 在 aclose 失败时兜底 terminate。SDK 内部名变动时静默回退
        # （_proc 保持 None，行为与旧版一致）。
        import mcp.client.stdio as _stdio_mod

        _orig_cpp = getattr(_stdio_mod, "_create_platform_compatible_process", None)
        # audit-fix: monkeypatch 段加模块级锁，防并发 connect 交错错记/提前还原
        async with _STDIO_PATCH_LOCK:
            if callable(_orig_cpp):

                async def _capturing_cpp(*args: Any, **kwargs: Any) -> Any:
                    proc = await _orig_cpp(*args, **kwargs)
                    self._proc = proc
                    return proc

                _stdio_mod._create_platform_compatible_process = _capturing_cpp
            try:
                read, write = await self._exit_stack.enter_async_context(
                    stdio_client(server_params)
                )
            finally:
                if callable(_orig_cpp):
                    _stdio_mod._create_platform_compatible_process = _orig_cpp
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

        # 超时：避免挂死的 MCP 永久卡死 agent loop
        import asyncio

        timeout = float(getattr(self, "timeout", 30.0) or 30.0)
        timeout = max(5.0, min(timeout, 300.0))
        try:
            result: CallToolResult = await asyncio.wait_for(
                self._session.call_tool(tool_name, safe_args),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            return f"[Error] MCP tool '{tool_name}' timed out after {timeout:.0f}s"

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
        try:
            await self._exit_stack.aclose()
        except Exception as e:
            # audit-fix: 跨 task 关 anyio cancel scope 会抛 RuntimeError，
            # 此时 stdio 子进程不会被 SDK 回收，需兜底 terminate 防孤儿进程
            logger.warning(f"MCP server '{self.name}' aclose failed: {e}")
            proc = getattr(self, "_proc", None)
            if proc is not None and getattr(proc, "returncode", None) is None:
                try:
                    proc.terminate()
                    logger.info(f"MCP server '{self.name}' stdio proc terminated (fallback)")
                except Exception as te:
                    logger.debug(f"MCP proc terminate skip: {te}")
            self._proc = None
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
