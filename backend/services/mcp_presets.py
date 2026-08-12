"""MCP 预制 + 内置 runner：消灭对全局 PATH 上 uvx 的依赖。

优先使用当前 Python 解释器旁的 Scripts/uvx.exe 或 python -m；
配置写入后可 load_mcp_tools + 可选 probe。
"""

from __future__ import annotations

import logging
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class McpPreset:
    id: str
    display_name: str
    aliases: tuple[str, ...]
    env_key: str
    # (kind, package_or_module, args_tail)
    # kind: module | uvx_pkg | npx_pkg | remote_url
    runners: tuple[tuple[str, str, tuple[str, ...]], ...] = ()
    probe_tool_substrings: tuple[str, ...] = ("search", "web")
    risk_level: str = "low"
    # stdio | streamable-http | sse；remote 预设可走 URL 直连
    transport: str = "stdio"
    url: str = ""
    # 连接/工具超时（mcp-remote 冷启动建议 ≥120）
    timeout: float = 30.0


PRESETS: tuple[McpPreset, ...] = (
    McpPreset(
        id="doubao-search",
        display_name="豆包搜索",
        aliases=(
            "doubao-search",
            "doubao",
            "豆包",
            "豆包搜索",
            "askecho",
            "askecho-search",
            "search-infinity",
        ),
        env_key="ASK_ECHO_SEARCH_INFINITY_API_KEY",
        runners=(
            (
                "uvx_pkg",
                "mcp-server-askecho-search-infinity>=0.2.0",
                ("mcp-server-askecho-search-infinity",),
            ),
        ),
        probe_tool_substrings=("web_search", "search"),
    ),
    McpPreset(
        id="tavily",
        display_name="Tavily 搜索",
        aliases=("tavily", "tavily-search", "tavily_search"),
        env_key="TAVILY_API_KEY",
        runners=(
            ("npx_pkg", "tavily-mcp@latest", ("-y", "tavily-mcp@latest")),
            ("uvx_pkg", "tavily-mcp", ("tavily-mcp",)),
        ),
        probe_tool_substrings=("tavily", "search"),
    ),
    McpPreset(
        id="firecrawl",
        display_name="Firecrawl",
        aliases=("firecrawl", "firecrawl-mcp"),
        env_key="FIRECRAWL_API_KEY",
        runners=(
            ("npx_pkg", "firecrawl-mcp", ("-y", "firecrawl-mcp")),
            ("uvx_pkg", "firecrawl-mcp", ("firecrawl-mcp",)),
        ),
        probe_tool_substrings=("firecrawl", "search", "scrape"),
    ),
    McpPreset(
        id="fetch",
        display_name="Fetch",
        aliases=("fetch", "mcp-server-fetch", "web-fetch"),
        env_key="",  # 无需 key
        # 新版 mcp 将 McpError 改名为 MCPError，未 pin 时 uvx 会 ImportError 秒退
        runners=(
            (
                "uvx_pkg",
                "mcp-server-fetch",
                ("--with", "mcp==1.6.0", "mcp-server-fetch"),
            ),
        ),
        probe_tool_substrings=("fetch",),
    ),
    # DeepWiki：优先 streamable-http 直连；失败时 agent 可改用 mcp-remote stdio 桥
    McpPreset(
        id="deepwiki",
        display_name="DeepWiki",
        aliases=(
            "deepwiki",
            "deep-wiki",
            "mcp-deepwiki",
            "devin-deepwiki",
        ),
        env_key="",
        runners=(),
        transport="streamable-http",
        url="https://mcp.deepwiki.com/mcp",
        timeout=60.0,
        probe_tool_substrings=("wiki", "ask", "question", "read"),
        risk_level="low",
    ),
    # 通用 mcp-remote 桥：stdio + npx，适合仅 stdio 的远程 HTTP MCP
    McpPreset(
        id="mcp-remote-deepwiki",
        display_name="DeepWiki (mcp-remote)",
        aliases=(
            "mcp-remote",
            "mcp-remote-deepwiki",
            "deepwiki-remote",
            "deepwiki-stdio",
        ),
        env_key="",
        runners=(
            (
                "npx_pkg",
                "mcp-remote@latest",
                (
                    "-y",
                    "mcp-remote@latest",
                    "https://mcp.deepwiki.com/mcp",
                    "--transport",
                    "http-only",
                ),
            ),
        ),
        timeout=120.0,
        probe_tool_substrings=("wiki", "ask", "question", "read"),
        risk_level="low",
    ),
)


def find_preset(name_or_alias: str) -> McpPreset | None:
    key = (name_or_alias or "").strip().lower()
    if not key:
        return None
    for p in PRESETS:
        if key == p.id.lower() or key in {a.lower() for a in p.aliases}:
            return p
        if key in p.display_name.lower():
            return p
    # 模糊：子串
    for p in PRESETS:
        for a in p.aliases:
            if a.lower() in key or key in a.lower():
                return p
    return None


def _scripts_dir() -> Path:
    return Path(sys.prefix) / ("Scripts" if sys.platform == "win32" else "bin")


def resolve_uvx_path() -> str | None:
    """解析本机可用的 uvx 绝对路径（优先解释器旁，不依赖 PATH）。"""
    try:
        from backend.core.host_commands import resolve_host_command

        r = resolve_host_command("uvx")
        if r and r.lower() != "uvx" and Path(r).is_file():
            return r
    except Exception:
        pass
    scripts = _scripts_dir()
    for name in ("uvx.exe", "uvx"):
        p = scripts / name
        if p.is_file():
            return str(p)
    home = Path.home()
    for p in (
        home / ".local" / "bin" / "uvx.exe",
        home / ".local" / "bin" / "uvx",
        home / "AppData" / "Local" / "Programs" / "tevarn" / "resources" / "python" / "Scripts" / "uvx.exe",
    ):
        if p.is_file():
            return str(p)
    which = shutil.which("uvx")
    return which


def resolve_npx_path() -> str | None:
    try:
        from backend.core.host_commands import resolve_host_command

        r = resolve_host_command("npx")
        if r and r.lower() not in ("npx", "npx.cmd") and Path(r).is_file():
            return r
        if r and Path(r).is_file():
            return r
    except Exception:
        pass
    for p in (
        Path(r"C:\Program Files\nodejs\npx.cmd"),
        Path(r"C:\Program Files\nodejs\npx.exe"),
        Path(r"C:\Program Files (x86)\nodejs\npx.cmd"),
    ):
        if p.is_file():
            return str(p)
    return shutil.which("npx") or shutil.which("npx.cmd")


def resolve_preset_command(preset: McpPreset) -> tuple[str, list[str], str]:
    """返回 (command, args, note)。remote URL 预设不走此函数。"""
    if not preset.runners:
        raise ValueError(f"preset '{preset.id}' has no stdio runners")
    py = sys.executable
    uvx = resolve_uvx_path()
    npx = resolve_npx_path()
    for kind, pkg, tail in preset.runners:
        if kind == "module":
            return py, ["-m", pkg, *list(tail)], f"python -m {pkg}"
        if kind == "npx_pkg":
            if npx:
                args = list(tail) if tail else ["-y", pkg]
                return npx, args, f"resolved npx: {npx}"
        if kind == "uvx_pkg":
            if uvx:
                # tail 可含 --with 等 uvx 选项；最终仍以 entrypoint 结尾
                if not tail:
                    args = ["--from", pkg, pkg]
                elif tail[0] == "--from":
                    args = list(tail)
                else:
                    args = ["--from", pkg, *list(tail)]
                return uvx, args, f"builtin uvx: {uvx}"
    # 最后回退
    kind0, pkg0, tail0 = preset.runners[0]
    if kind0 == "npx_pkg":
        return "npx", list(tail0) if tail0 else ["-y", pkg0], "fallback bare npx"
    return (
        "uvx",
        ["--from", pkg0, *list(tail0)],
        "fallback bare uvx (PATH)",
    )


async def ensure_mcp_preset(
    *,
    preset: McpPreset,
    api_key: str = "",
    server_name: str | None = None,
    reload: bool = True,
    probe: bool = True,
) -> dict[str, Any]:
    """写入/更新 MCP 配置，reload 工具，可选 probe。"""
    from backend.mcp_hub.normalize import is_url_transport, normalize_transport
    from backend.repositories.mcp_server_repo import AsyncMCPServerRepository
    from backend.schemas.mcp import MCPServerCreate, MCPServerUpdate

    repo = AsyncMCPServerRepository()
    name = (server_name or preset.id).strip() or preset.id
    transport = normalize_transport(preset.transport) or "stdio"
    timeout = float(getattr(preset, "timeout", 30.0) or 30.0)
    url = (preset.url or "").strip() or None
    command: str | None = None
    args: list[str] = []
    runner_note = ""

    if is_url_transport(transport) and url:
        runner_note = f"{transport}: {url}"
    else:
        transport = "stdio"
        command, args, runner_note = resolve_preset_command(preset)

    env: dict[str, str] = {}
    if preset.env_key and api_key:
        env[preset.env_key] = api_key

    existing = await repo.get_by_name(name)
    if existing is None:
        # 也按别名找
        all_s = await repo.list_all()
        for s in all_s or []:
            sn = (s.name or "").lower()
            if sn in {a.lower() for a in preset.aliases} or sn == preset.id.lower():
                existing = s
                name = s.name
                break

    if existing is None:
        obj = await repo.create(
            MCPServerCreate(
                name=name,
                transport=transport,
                command=command,
                args=list(args),
                url=url,
                env=env,
                enabled=True,
                timeout=timeout,
                risk_level=preset.risk_level or "low",
            )
        )
        action = "created"
    else:
        old_env = dict(existing.env or {})
        if preset.env_key and api_key:
            old_env[preset.env_key] = api_key
        update_kw: dict[str, Any] = {
            "transport": transport,
            "enabled": True,
            "timeout": timeout,
        }
        if is_url_transport(transport):
            update_kw["url"] = url
            update_kw["command"] = None
            update_kw["args"] = []
        else:
            update_kw["command"] = command
            update_kw["args"] = list(args)
        if old_env:
            update_kw["env"] = old_env
        obj = await repo.update(existing.id, MCPServerUpdate(**update_kw))
        action = "updated"

    tools_registered = 0
    reload_error = None
    connect_error = None
    if reload:
        try:
            from backend.mcp_hub.client import get_mcp_manager
            from backend.mcp_hub.service import sync_mcp_runtime

            sname = str(getattr(obj, "name", None) or name or "")
            rt = await sync_mcp_runtime(only_server=sname or None)
            if not rt.get("ok") or sname not in (rt.get("connected") or []):
                connect_error = (
                    rt.get("connect_error")
                    or rt.get("error")
                    or rt.get("warning")
                    or "not connected"
                )
                reload_error = str(connect_error)
            mgr = get_mcp_manager()
            client = mgr.get_client(sname)
            if client is not None:
                try:
                    listed = await client.list_tools()
                    tools_registered = len(getattr(listed, "tools", None) or [])
                except Exception as e:
                    reload_error = f"list_tools: {e}"
        except Exception as e:
            reload_error = str(e)
            logger.warning("MCP reload after preset failed: %s", e)

    probe_ok = None
    probe_detail = ""
    if probe and tools_registered > 0:
        probe_ok, probe_detail = await _probe_mcp_search(obj.name if obj else name, preset)

    ok = tools_registered > 0 and not connect_error
    conclude = bool(connect_error) or (reload and tools_registered == 0)
    return {
        "ok": ok,
        "action": action,
        "server_name": obj.name if obj else name,
        "server_id": str(obj.id) if obj else "",
        "command": command,
        "args": list(args),
        "url": url,
        "transport": transport,
        "timeout": timeout,
        "runner_note": runner_note,
        "env_key": preset.env_key,
        "tools_registered": tools_registered,
        "reload_error": reload_error,
        "connect_error": connect_error,
        "probe_ok": probe_ok,
        "probe_detail": probe_detail,
        "preset_id": preset.id,
        "db_written": True,
        "conclude": conclude,
    }


async def _probe_mcp_search(server_name: str, preset: McpPreset) -> tuple[bool | None, str]:
    """轻量探活：找一个 search 类工具并调用（失败不抛）。"""
    try:
        from backend.mcp_hub.client import get_mcp_manager

        mgr = get_mcp_manager()
        client = mgr.get_client(server_name)
        if client is None:
            return False, "server not connected"
        listed = await client.list_tools()
        tools = list(getattr(listed, "tools", None) or [])
        pick = None
        for t in tools:
            n = str(getattr(t, "name", "") or "").lower()
            if any(s in n for s in preset.probe_tool_substrings):
                pick = getattr(t, "name", None)
                break
        if not pick and tools:
            pick = getattr(tools[0], "name", None)
        if not pick:
            return False, "no tools"
        # 最小参数尝试
        args: dict[str, Any] = {"query": "ping"}
        try:
            await client.call_tool(str(pick), args)
            return True, f"called {pick}"
        except Exception as e1:
            # 有的工具要 max_results
            try:
                await client.call_tool(str(pick), {"query": "ping", "max_results": 5})
                return True, f"called {pick} with max_results"
            except Exception as e2:
                return False, f"{pick}: {e1}; retry: {e2}"
    except Exception as e:
        return False, str(e)
