"""
MCP 服务模块

- 从数据库读取 MCP Server 配置
- 连接启用 server、注册到 ToolRegistry
- sync_mcp_runtime：热同步（支持 only_server）
- 启动时 rewire 裸 npx/uvx → 绝对路径
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from backend.mcp_hub.client import MCPClientManager, MCPServerConfig
from backend.repositories.mcp_server_repo import AsyncMCPServerRepository
from backend.tools.adapters.mcp_adapter import (
    register_mcp_server_tools,
    unregister_all_mcp_tools,
    unregister_mcp_server_tools,
)

logger = logging.getLogger(__name__)


def _resolve_cmd(cmd: str | None) -> str | None:
    """运行时解析命令路径；不改 DB。

    npx/npm 允许解析到 .cmd（Connect 时用），但 rewire 不会把 .cmd 写回 DB。
    """
    if not cmd:
        return cmd
    raw = str(cmd).strip()
    try:
        from backend.core.host_commands import resolve_host_command

        low = raw.lower()
        # 已是绝对路径则不动
        if "\\" in raw or "/" in raw:
            try:
                if Path(raw).is_file():
                    return raw
            except Exception:
                pass
        if low in ("npx", "npx.cmd", "uvx", "uvx.exe", "uv", "uv.exe", "node", "node.exe", "npm", "npm.cmd") or (
            "\\" not in raw and "/" not in raw
        ):
            resolved = resolve_host_command(raw)
            if resolved and Path(resolved).is_file():
                return resolved
    except Exception:
        pass
    return raw


def _row_to_config(s: Any) -> MCPServerConfig:
    transport = str(getattr(s, "transport", "") or "stdio")
    try:
        from backend.mcp_hub.normalize import normalize_transport

        transport = normalize_transport(transport) or transport
    except Exception:
        pass
    return MCPServerConfig(
        name=s.name,
        transport=transport,
        command=_resolve_cmd(s.command),
        args=list(s.args or []),
        url=s.url,
        env=dict(s.env or {}),
        enabled=bool(s.enabled),
        timeout=float(getattr(s, "timeout", 30.0) or 30.0),
    )


def _should_persist_rewire(original: str, resolved: str) -> bool:
    """是否把解析结果写回 DB。

    禁止持久化 npx.cmd / npm.cmd：跨机路径易失效，且 .cmd 在部分 CreateProcess
    场景下不可靠；连接时由 _resolve_cmd 动态解析即可。
    仅持久化真实可执行文件（uvx/uv/node 的 .exe 或 POSIX 二进制）。
    """
    if not resolved or resolved == original:
        return False
    low = Path(resolved).name.lower()
    # 永不把 shell 包装器写进 DB
    if low in ("npx.cmd", "npx.bat", "npm.cmd", "npm.bat", "npx", "npm"):
        return False
    if low.endswith(".cmd") or low.endswith(".bat") or low.endswith(".ps1"):
        return False
    base = original.strip().lower().removesuffix(".exe").removesuffix(".cmd")
    # 只对已知工具做 DB rewire
    if base not in ("uvx", "uv", "node"):
        return False
    try:
        return Path(resolved).is_file()
    except Exception:
        return False


async def rewire_bare_mcp_commands() -> int:
    """把 DB 里裸 uvx/uv/node 写成绝对路径；npx 保持裸名，运行时再解析。"""
    from backend.core.host_commands import resolve_host_command
    from backend.schemas.mcp import MCPServerUpdate

    repo = AsyncMCPServerRepository()
    n = 0
    try:
        servers = await repo.list_all()
    except Exception:
        return 0
    for s in servers or []:
        cmd = (s.command or "").strip()
        if not cmd:
            continue
        # 已是路径则跳过
        if "\\" in cmd or "/" in cmd:
            continue
        low = cmd.lower().removesuffix(".exe").removesuffix(".cmd")
        # 不 rewire npx/npm 进 DB
        if low not in ("uvx", "uv", "node"):
            continue
        resolved = resolve_host_command(cmd)
        try:
            if not _should_persist_rewire(cmd, resolved or ""):
                continue
            await repo.update(s.id, MCPServerUpdate(command=resolved))
            n += 1
            logger.info("rewired MCP %s command %r → %r", s.name, cmd, resolved)
        except Exception as e:
            logger.debug("rewire skip %s: %s", s.name, e)
    return n


async def load_mcp_tools(manager: MCPClientManager | None = None) -> MCPClientManager:
    """全量加载（兼容旧调用）；内部走 sync_mcp_runtime。"""
    await sync_mcp_runtime()
    if manager is None:
        from backend.mcp_hub.client import get_mcp_manager

        manager = get_mcp_manager()
    return manager


async def sync_mcp_runtime(only_server: str | None = None) -> dict[str, Any]:
    """热同步 MCP 连接与 ToolRegistry。

    only_server:
      - None：全量 close_all → 连全部 enabled → 重挂工具
      - 名字：只卸/重连该 server（避免全局卸光）

    返回:
      {ok, connected, registered, unregistered, error?, warning?}
    """
    from backend.mcp_hub.client import get_mcp_manager

    try:
        await rewire_bare_mcp_commands()
    except Exception as e:
        logger.debug("rewire_bare_mcp_commands skip: %s", e)

    manager = get_mcp_manager()
    repo = AsyncMCPServerRepository()
    only = (only_server or "").strip() or None

    try:
        if only:
            # 安全热更：先 reconnect（失败保留旧连接），成功后再换 registry
            unregistered = 0
            registered = 0
            warning = None
            row = await repo.get_by_name(only)
            if row is None:
                try:
                    unregistered = unregister_mcp_server_tools(only)
                except Exception as e:
                    logger.debug("unregister_mcp_server_tools %s: %s", only, e)
                try:
                    await manager.disconnect(only)
                except Exception as e:
                    logger.debug("disconnect missing server %s: %s", only, e)
                logger.info(
                    "sync_mcp_runtime: only_server=%s not in DB (unregistered=%s)",
                    only,
                    unregistered,
                )
                return {
                    "ok": True,
                    "connected": manager.list_connected(),
                    "registered": 0,
                    "unregistered": unregistered,
                    "warning": f"server '{only}' not in DB",
                }

            if not row.enabled:
                try:
                    unregistered = unregister_mcp_server_tools(only)
                except Exception as e:
                    logger.debug("unregister_mcp_server_tools %s: %s", only, e)
                try:
                    await manager.disconnect(only)
                except Exception as e:
                    logger.debug("disconnect disabled server %s: %s", only, e)
                return {
                    "ok": True,
                    "connected": manager.list_connected(),
                    "registered": 0,
                    "unregistered": unregistered,
                    "warning": f"server '{only}' disabled",
                }

            cfg = _row_to_config(row)
            client = await manager.reconnect(cfg)
            if client is None:
                detail = ""
                try:
                    detail = (manager.pop_last_error(only) or "").strip()
                except Exception:
                    detail = ""
                if not detail:
                    detail = "unknown connect error"
                warning = (
                    f"Failed to connect MCP server '{only}': {detail} "
                    f"(kept previous if any)"
                )
                logger.warning("%s", warning)
                return {
                    "ok": False,
                    "connected": manager.list_connected(),
                    "registered": 0,
                    "unregistered": 0,
                    "warning": warning,
                    "error": warning,
                    "connect_error": detail,
                    "conclude": True,
                }

            try:
                unregistered = unregister_mcp_server_tools(only)
            except Exception as e:
                logger.debug("unregister_mcp_server_tools %s: %s", only, e)
            try:
                registered = await register_mcp_server_tools(
                    cfg.name,
                    client,
                    risk_level=getattr(row, "risk_level", None),
                    tools_include=getattr(row, "tools_include", None),
                    tools_exclude=getattr(row, "tools_exclude", None),
                )
            except Exception as e:
                warning = str(e)
                logger.warning("register tools after reconnect %s: %s", only, e)

            connected = manager.list_connected()
            logger.info(
                "sync_mcp_runtime: only=%s connected=%s registered=%s warning=%s",
                only,
                connected,
                registered,
                warning,
            )
            return {
                "ok": warning is None,
                "connected": connected,
                "registered": registered,
                "unregistered": unregistered,
                "warning": warning,
                "error": warning,
            }

        unregistered = 0
        try:
            unregistered = unregister_all_mcp_tools()
        except Exception as e:
            logger.debug("unregister_all_mcp_tools: %s", e)

        await manager.close_all()

        servers = await repo.get_all_enabled()
        configs = [_row_to_config(s) for s in (servers or [])]
        meta_by_name = {s.name: s for s in (servers or [])}

        await manager.connect(configs)

        registered = 0
        for name in manager.list_connected():
            client = manager.get_client(name)
            if client is None:
                continue
            row = meta_by_name.get(name)
            try:
                n = await register_mcp_server_tools(
                    name,
                    client,
                    risk_level=getattr(row, "risk_level", None) if row else None,
                    tools_include=getattr(row, "tools_include", None) if row else None,
                    tools_exclude=getattr(row, "tools_exclude", None) if row else None,
                )
                registered += int(n or 0)
                logger.info(
                    "Registered %s MCP tools from server '%s' (include=%s exclude=%s)",
                    n,
                    name,
                    getattr(row, "tools_include", None) if row else None,
                    getattr(row, "tools_exclude", None) if row else None,
                )
            except Exception as e:
                logger.warning("Failed to register MCP tools from '%s': %s", name, e)

        connected = manager.list_connected()
        enabled_names = [c.name for c in configs]
        warning = None
        missing = [n for n in enabled_names if n not in connected]
        connect_errors: dict[str, str] = {}
        if missing:
            bits: list[str] = []
            for n in missing:
                detail = ""
                try:
                    detail = (manager.pop_last_error(n) or "").strip()
                except Exception:
                    detail = ""
                if detail:
                    connect_errors[n] = detail
                    bits.append(f"{n} ({detail})")
                else:
                    bits.append(n)
            warning = f"failed to connect: {', '.join(bits)}"

        logger.info(
            "sync_mcp_runtime: connected=%s registered=%s warning=%s",
            connected,
            registered,
            warning,
        )
        return {
            "ok": True if not missing else False,
            "connected": connected,
            "registered": registered,
            "unregistered": unregistered,
            "warning": warning,
            "error": warning,
            "connect_errors": connect_errors or None,
            "conclude": bool(missing),
        }
    except Exception as e:
        logger.exception("sync_mcp_runtime failed: %s", e)
        return {
            "ok": False,
            "connected": [],
            "registered": 0,
            "unregistered": 0,
            "error": str(e),
        }


async def get_mcp_status() -> list[dict]:
    """获取所有 MCP Server 的连接状态（enabled/connected 分离）。"""
    from backend.mcp_hub.client import get_mcp_manager

    manager = get_mcp_manager()
    repo = AsyncMCPServerRepository()
    try:
        servers = await repo.list_all()
    except Exception:
        servers = await repo.get_all_enabled()

    status: list[dict] = []
    for s in servers or []:
        client = manager.get_client(s.name)
        connected = client is not None and bool(getattr(client, "_initialized", False))
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
                "enabled": bool(s.enabled),
                "connected": connected,
                "tool_count": tool_count,
                "error": error,
            }
        )
    return status
