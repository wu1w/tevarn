"""
MCP 客户端基础封装

对齐 Hermes / OpenClaw 实践：
- stdio：command/args/env（env 与系统 PATH 合并，密钥不覆盖空值）
- Windows：npx → npx.cmd
- 远程：sse + streamable-http（官方 MCP Streamable HTTP）
- 单 server 重连：先连新再换旧，避免「先 close_all 再失败」导致工具全丢
- 连接超时：用 config.timeout 包一层 wait_for（mcp-remote 冷启动可调高）
"""

from __future__ import annotations

import asyncio
import logging
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
    transport: str  # "stdio" | "sse" | "streamable-http"
    command: str | None = None
    args: list[str] | None = None
    url: str | None = None
    env: dict[str, str] | None = None
    enabled: bool = True
    timeout: float = 30.0


def _normalize_transport(transport: str | None) -> str:
    try:
        from backend.mcp_hub.normalize import normalize_transport

        return normalize_transport(transport)
    except Exception:
        t = (transport or "").strip().lower().replace(" ", "").replace("_", "-")
        if t in ("http", "https", "streamablehttp"):
            return "streamable-http"
        return t


def _connect_timeout_seconds(config: MCPServerConfig) -> float:
    """连接超时：尊重 config.timeout；stdio + mcp-remote/npx 冷启动抬底。"""
    try:
        base = float(getattr(config, "timeout", None) or 30.0)
    except (TypeError, ValueError):
        base = 30.0
    base = max(10.0, min(base, 300.0))
    transport = _normalize_transport(config.transport)
    if transport == "stdio":
        blob = " ".join(
            [
                str(config.command or ""),
                *list(config.args or []),
                str(config.name or ""),
            ]
        ).lower()
        # npx -y mcp-remote 首次下载可能 >30s
        if "mcp-remote" in blob or "mcp_remote" in blob:
            base = max(base, 120.0)
        elif any(x in blob for x in ("npx", "uvx", "@latest")):
            base = max(base, 60.0)
    return base


class MCPClient:
    """MCP 客户端连接管理器"""

    def __init__(self, config: MCPServerConfig):
        # 规范 transport，避免 DB 里 http / streamable_http 直穿
        try:
            config.transport = _normalize_transport(config.transport) or config.transport
        except Exception:
            pass
        self.config = config
        self.name = config.name
        self._session: ClientSession | None = None
        self._exit_stack = AsyncExitStack()
        self._initialized = False
        self._proc: Any | None = None
        # 缓存 list_tools 结果，status 探测不必每次 RPC
        self._cached_tool_count: int = 0
        self._cached_remote_tools: list[dict[str, str]] = []

    async def connect(self) -> None:
        if not self.config.enabled:
            raise RuntimeError(f"MCP server '{self.name}' is disabled")

        transport = _normalize_transport(self.config.transport)
        self.config.transport = transport or self.config.transport
        connect_timeout = _connect_timeout_seconds(self.config)

        async def _do_connect() -> None:
            if transport == "stdio":
                await self._connect_stdio()
            elif transport == "sse":
                await self._connect_sse()
            elif transport == "streamable-http":
                await self._connect_streamable_http()
            else:
                raise ValueError(
                    f"Unsupported transport: {self.config.transport} "
                    f"(supported: stdio, sse, streamable-http)"
                )

        try:
            await asyncio.wait_for(_do_connect(), timeout=connect_timeout)
        except asyncio.TimeoutError as e:
            raise TimeoutError(
                f"MCP server '{self.name}' connect timed out after "
                f"{connect_timeout:.0f}s (transport={transport})"
            ) from e

        self._initialized = True
        try:
            tools = await self.list_tools()
            self._cached_tool_count = len(tools.tools)
            self._cached_remote_tools = [
                {
                    "name": t.name,
                    "description": (t.description or "")[:500],
                }
                for t in tools.tools
            ]
        except Exception:
            self._cached_tool_count = 0
            self._cached_remote_tools = []
        logger.info(
            "MCP server '%s' connected (transport=%s tools≈%s)",
            self.name,
            transport,
            self._cached_tool_count,
        )

    async def _connect_stdio(self) -> None:
        if not self.config.command:
            raise ValueError(f"MCP server '{self.name}' stdio transport requires command")

        from backend.mcp_hub.normalize import merge_env, normalize_stdio_command_args

        command, args = normalize_stdio_command_args(
            self.name,
            self.config.command,
            list(self.config.args or []),
        )
        env = merge_env(None, self.config.env or {})
        # 始终带完整 os.environ，避免子进程无 PATH（OpenClaw/Hermes 同款）
        command, args, env = self._prepare_stdio_launch(command or "", args, env or None)

        server_params = StdioServerParameters(
            command=command,
            args=args,
            env=env,
        )
        import mcp.client.stdio as _stdio_mod

        _orig_cpp = getattr(_stdio_mod, "_create_platform_compatible_process", None)
        async with _STDIO_PATCH_LOCK:
            if callable(_orig_cpp):

                async def _capturing_cpp(*a: Any, **kw: Any) -> Any:
                    proc = await _orig_cpp(*a, **kw)
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
        session = await self._exit_stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        self._session = session

    @staticmethod
    def _prepare_stdio_launch(
        command: str,
        args: list[str],
        env: dict[str, str] | None,
    ) -> tuple[str, list[str], dict[str, str] | None]:
        import os
        import shutil
        import sys

        cmd = (command or "").strip()
        out_args = list(args or [])
        # Electron 精简 PATH：优先 host_commands 解析 npx/uvx/node
        # 子进程 env：白名单 + PATH 补全 + 用户 extra（勿整表继承 os.environ 灌密钥）
        used_curated_env = False
        try:
            from backend.core.host_commands import (
                build_process_env,
                resolve_host_command,
            )

            resolved = resolve_host_command(cmd)
            if resolved and resolved != cmd:
                cmd = resolved
            env = build_process_env(env)
            used_curated_env = True
        except Exception:
            pass
        if sys.platform == "win32" and cmd:
            base = os.path.basename(cmd).lower()
            if base in {"npx", "npm", "node", "npx.cmd", "npm.cmd", "node.exe"}:
                resolved = shutil.which(cmd) or shutil.which(f"{base.split('.')[0]}.cmd")
                if not resolved and "npx" in base:
                    for cand in (
                        os.path.join(
                            os.environ.get("ProgramFiles", r"C:\Program Files"),
                            "nodejs",
                            "npx.cmd",
                        ),
                        os.path.join(
                            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                            "nodejs",
                            "npx.cmd",
                        ),
                    ):
                        if os.path.isfile(cand):
                            resolved = cand
                            break
                if resolved:
                    cmd = resolved
            elif base in {"uvx", "uv", "uvx.exe", "uv.exe"}:
                # Electron 子进程 PATH 常缺用户 .local\bin；补齐 uvx
                resolved = shutil.which(cmd) or shutil.which(base.split(".")[0])
                if not resolved:
                    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
                    for cand in (
                        os.path.join(home, ".local", "bin", "uvx.exe"),
                        os.path.join(home, ".local", "bin", "uv.exe"),
                        os.path.join(home, ".cargo", "bin", "uvx.exe"),
                        os.path.join(home, ".cargo", "bin", "uv.exe"),
                        os.path.join(
                            os.environ.get("LOCALAPPDATA", ""),
                            "Programs",
                            "uv",
                            "uvx.exe",
                        ),
                    ):
                        if cand and os.path.isfile(cand):
                            # uvx 可能是 uv 的别名；优先 uvx.exe
                            if base.startswith("uvx") and cand.endswith("uv.exe"):
                                # uv tool run 风格：uvx ≈ uvx.exe 独立入口
                                uvx_sib = os.path.join(os.path.dirname(cand), "uvx.exe")
                                resolved = uvx_sib if os.path.isfile(uvx_sib) else cand
                            else:
                                resolved = cand
                            break
                if resolved:
                    cmd = resolved
        if not used_curated_env:
            # host_commands 不可用时的回退：仍尽量用白名单；最后才全表继承
            try:
                from backend.core.host_commands import build_process_env as _bpe

                env = _bpe(env)
            except Exception:
                if env is not None:
                    merged = {str(k): str(v) for k, v in os.environ.items()}
                    merged.update(
                        {str(k): str(v) for k, v in env.items() if v is not None}
                    )
                    env = merged
                else:
                    env = {str(k): str(v) for k, v in os.environ.items()}
        return cmd, out_args, env

    async def _connect_sse(self) -> None:
        if not self.config.url:
            raise ValueError(f"MCP server '{self.name}' sse transport requires url")

        read, write = await self._exit_stack.enter_async_context(
            sse_client(self.config.url)
        )
        session = await self._exit_stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        self._session = session

    async def _connect_streamable_http(self) -> None:
        """官方 Streamable HTTP（MCP 2025-03+）；DeepWiki 等远程服务首选。

        兼容 mcp 1.x（streamablehttp_client → 3-tuple）与 2.x
        （streamable_http_client → 2-tuple / TransportStreams）。
        """
        if not self.config.url:
            raise ValueError(
                f"MCP server '{self.name}' streamable-http transport requires url"
            )

        client_fn = None
        legacy = False
        try:
            from mcp.client.streamable_http import streamable_http_client as client_fn
        except ImportError:
            try:
                from mcp.client.streamable_http import (
                    streamablehttp_client as client_fn,
                )

                legacy = True
            except ImportError as e:
                raise RuntimeError(
                    "mcp package missing streamable_http client; upgrade `mcp`"
                ) from e

        try:
            http_timeout = float(getattr(self.config, "timeout", None) or 30.0)
        except (TypeError, ValueError):
            http_timeout = 30.0
        http_timeout = max(10.0, min(http_timeout, 300.0))

        if legacy:
            # mcp 1.x: timeout / sse_read_timeout 关键字参数
            streams = await self._exit_stack.enter_async_context(
                client_fn(
                    self.config.url,
                    timeout=http_timeout,
                    sse_read_timeout=max(http_timeout, 300.0),
                )
            )
        else:
            # mcp 2.x: 可选自定义 http_client；失败则用 SDK 默认
            try:
                from mcp.client.streamable_http import create_mcp_http_client

                try:
                    import httpx2 as _httpx  # type: ignore
                except ImportError:
                    import httpx as _httpx  # type: ignore

                timeout_obj = _httpx.Timeout(
                    http_timeout, read=max(http_timeout, 300.0)
                )
                http_client = create_mcp_http_client(timeout=timeout_obj)
                # 我们创建的 client 需自行管理生命周期
                await self._exit_stack.enter_async_context(http_client)
                streams = await self._exit_stack.enter_async_context(
                    client_fn(self.config.url, http_client=http_client)
                )
            except Exception as e:
                logger.debug(
                    "streamable-http custom http_client skip (%s); use defaults",
                    e,
                )
                streams = await self._exit_stack.enter_async_context(
                    client_fn(self.config.url)
                )

        # 1.x: (read, write, get_session_id)；2.x: (read, write)
        try:
            read, write = streams[0], streams[1]
        except Exception as e:
            raise RuntimeError(
                f"unexpected streamable-http streams shape: {type(streams)!r}"
            ) from e

        session = await self._exit_stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        self._session = session

    async def list_tools(self) -> ListToolsResult:
        if self._session is None or not self._initialized:
            raise RuntimeError(f"MCP server '{self.name}' not connected")
        return await self._session.list_tools()

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        if self._session is None or not self._initialized:
            raise RuntimeError(f"MCP server '{self.name}' not connected")

        import json as _json

        safe_args: dict[str, Any] = {}
        if isinstance(arguments, dict):
            for k, v in arguments.items():
                ks = str(k)
                if ks.startswith("_") or ks in (
                    "ws_manager",
                    "connection_manager",
                    "user_id",
                ):
                    continue
                if "ConnectionManager" in type(v).__name__:
                    continue
                try:
                    safe_args[ks] = _json.loads(_json.dumps(v, default=str))
                except Exception:
                    safe_args[ks] = str(v)

        try:
            timeout = float(getattr(self.config, "timeout", None) or 30.0)
        except (TypeError, ValueError):
            timeout = 30.0
        timeout = max(5.0, min(timeout, 300.0))
        try:
            result: CallToolResult = await asyncio.wait_for(
                self._session.call_tool(tool_name, safe_args),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            return f"[Error] MCP tool '{tool_name}' timed out after {timeout:.0f}s"
        except Exception as e:
            # 连接半死：让上层可触发 reconnect
            err = f"{type(e).__name__}: {e}"
            logger.warning("MCP call_tool failed server=%s tool=%s: %s", self.name, tool_name, err)
            return f"[Error] MCP tool '{tool_name}' failed: {err}"

        parts: list[str] = []
        for content in result.content:
            if isinstance(content, TextContent):
                parts.append(content.text)
            else:
                parts.append(str(content))
        return chr(10).join(parts)

    async def close(self) -> None:
        try:
            await self._exit_stack.aclose()
        except Exception as e:
            logger.warning(f"MCP server '{self.name}' aclose failed: {e}")
            proc = getattr(self, "_proc", None)
            if proc is not None and getattr(proc, "returncode", None) is None:
                try:
                    proc.terminate()
                    logger.info(
                        f"MCP server '{self.name}' stdio proc terminated (fallback)"
                    )
                except Exception as te:
                    logger.debug(f"MCP proc terminate skip: {te}")
            self._proc = None
        self._session = None
        self._initialized = False
        self._cached_tool_count = 0
        # 新的 ExitStack，便于同实例不复用（我们通常新建 client）
        self._exit_stack = AsyncExitStack()
        logger.info(f"MCP server '{self.name}' disconnected")

    async def __aenter__(self) -> MCPClient:
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()


class MCPClientManager:
    """管理多个 MCPClient；支持单服安全重连。"""

    def __init__(self):
        self._clients: dict[str, MCPClient] = {}
        self._lock = asyncio.Lock()
        # 最近一次连接失败原因（供 sync/manage_mcp 写清错误，避免 agent 盲重试）
        self._last_errors: dict[str, str] = {}

    async def connect(self, configs: list[MCPServerConfig]) -> None:
        for config in configs:
            if not config.enabled:
                continue
            await self.reconnect(config)

    def last_error(self, server_name: str) -> str | None:
        return self._last_errors.get(server_name)

    def pop_last_error(self, server_name: str) -> str | None:
        return self._last_errors.pop(server_name, None)

    async def reconnect(self, config: MCPServerConfig) -> MCPClient | None:
        """先连新后换旧（Hermes/OpenClaw 式安全热更）。失败则保留旧连接。"""
        if not config.enabled:
            await self.disconnect(config.name)
            self._last_errors.pop(config.name, None)
            return None

        async with self._lock:
            new_client = MCPClient(config)
            try:
                await new_client.connect()
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                self._last_errors[config.name] = err
                logger.warning(
                    "Failed to connect MCP server '%s': %s", config.name, err
                )
                try:
                    await new_client.close()
                except Exception:
                    pass
                return self._clients.get(config.name)

            self._last_errors.pop(config.name, None)
            old = self._clients.pop(config.name, None)
            self._clients[config.name] = new_client
            if old is not None:
                try:
                    await old.close()
                except Exception as e:
                    logger.debug("old MCP close skip %s: %s", config.name, e)
            return new_client

    async def disconnect(self, server_name: str) -> None:
        async with self._lock:
            old = self._clients.pop(server_name, None)
            self._last_errors.pop(server_name, None)
        if old is not None:
            try:
                await old.close()
            except Exception:
                pass

    def get_client(self, server_name: str) -> MCPClient | None:
        return self._clients.get(server_name)

    def list_connected(self) -> list[str]:
        return [
            n
            for n, c in self._clients.items()
            if getattr(c, "_initialized", False)
        ]

    async def close_all(self) -> None:
        async with self._lock:
            clients = list(self._clients.values())
            self._clients.clear()
            self._last_errors.clear()
        await asyncio.gather(
            *[client.close() for client in clients],
            return_exceptions=True,
        )

    async def __aenter__(self) -> MCPClientManager:
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close_all()


_mcp_manager: MCPClientManager | None = None


def get_mcp_manager() -> MCPClientManager:
    global _mcp_manager
    if _mcp_manager is None:
        _mcp_manager = MCPClientManager()
    return _mcp_manager


def reset_mcp_manager() -> None:
    global _mcp_manager
    _mcp_manager = None
