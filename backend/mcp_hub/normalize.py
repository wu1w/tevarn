"""MCP 配置规范化（对齐 Hermes/OpenClaw 商店实践）。

- 纠正已知错误包名（如 @tavily/mcp-server → tavily-mcp）
- 合并 env，拒绝空值/脱敏占位符覆盖真密钥
- Windows 下 npx → 可解析命令
"""

from __future__ import annotations

import os
import re
import sys
from typing import Any  # noqa: F401 — used by normalize helpers

# 错误/过时 npm 包 → 正确 args
_BAD_NPM_ARGS: dict[str, list[str]] = {
    "@tavily/mcp-server": ["-y", "tavily-mcp@latest"],
    "@tavily/mcp": ["-y", "tavily-mcp@latest"],
    "tavily-mcp-server": ["-y", "tavily-mcp@latest"],
}

# 按 server 名的默认安装模板（与 mcp_store curated 对齐）
_DOUBAO_SEARCH_ARGS = [
    "--from",
    "mcp-server-askecho-search-infinity>=0.2.0",
    "mcp-server-askecho-search-infinity",
]
_SERVER_DEFAULTS: dict[str, dict[str, Any]] = {
    "tavily": {
        "command": "npx",
        "args": ["-y", "tavily-mcp@latest"],
        "env_keys": ["TAVILY_API_KEY"],
    },
    "firecrawl": {
        "command": "npx",
        "args": ["-y", "firecrawl-mcp"],
        "env_keys": ["FIRECRAWL_API_KEY"],
    },
    # 豆包搜索 / 火山引擎 Search Infinity 官方 MCP（多别名）
    "doubao-search": {
        "command": "uvx",
        "args": list(_DOUBAO_SEARCH_ARGS),
        "env_keys": [
            "ASK_ECHO_SEARCH_INFINITY_API_KEY",
            "VOLCENGINE_ACCESS_KEY",
            "VOLCENGINE_SECRET_KEY",
        ],
    },
    "doubao": {
        "command": "uvx",
        "args": list(_DOUBAO_SEARCH_ARGS),
        "env_keys": ["ASK_ECHO_SEARCH_INFINITY_API_KEY"],
    },
    "askecho": {
        "command": "uvx",
        "args": list(_DOUBAO_SEARCH_ARGS),
        "env_keys": ["ASK_ECHO_SEARCH_INFINITY_API_KEY"],
    },
    "askecho-search": {
        "command": "uvx",
        "args": list(_DOUBAO_SEARCH_ARGS),
        "env_keys": ["ASK_ECHO_SEARCH_INFINITY_API_KEY"],
    },
    "mcp-server-askecho-search-infinity": {
        "command": "uvx",
        "args": list(_DOUBAO_SEARCH_ARGS),
        "env_keys": ["ASK_ECHO_SEARCH_INFINITY_API_KEY"],
    },
    # DeepWiki 官方远程：stdio 经 mcp-remote 桥接 streamable-http（冷启动需更长 timeout）
    "deepwiki": {
        "command": "npx",
        "args": [
            "-y",
            "mcp-remote@latest",
            "https://mcp.deepwiki.com/mcp",
            "--transport",
            "http-only",
        ],
        "env_keys": [],
        "timeout": 120.0,
    },
    "mcp-remote": {
        "command": "npx",
        "args": ["-y", "mcp-remote@latest"],
        "env_keys": [],
        "timeout": 120.0,
    },
}

_REDACTED = frozenset(
    {
        "",
        "***",
        "****",
        "[redacted]",
        "redacted",
        "<redacted>",
        "your_api_key",
        "your-api-key",
        "changeme",
    }
)

_ENV_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")

# transport 别名 → 规范名（DB / schema / client 统一）
_TRANSPORT_ALIASES: dict[str, str] = {
    "stdio": "stdio",
    "sse": "sse",
    "streamable-http": "streamable-http",
    "streamable_http": "streamable-http",
    "streamablehttp": "streamable-http",
    "http": "streamable-http",
    "https": "streamable-http",
}
SUPPORTED_TRANSPORTS = frozenset({"stdio", "sse", "streamable-http"})


def normalize_transport(value: str | None) -> str:
    """规范化 transport；未知值原样小写返回（由调用方校验）。"""
    raw = (value or "").strip().lower().replace(" ", "")
    if not raw:
        return ""
    return _TRANSPORT_ALIASES.get(raw, raw)


def is_url_transport(transport: str | None) -> bool:
    return normalize_transport(transport) in ("sse", "streamable-http")


def is_redacted_secret(value: Any) -> bool:
    s = str(value or "").strip()
    if not s:
        return True
    if s.lower() in _REDACTED:
        return True
    if s.startswith("tvly-***") or s.endswith("***"):
        return True
    return False


def expand_env_value(value: str) -> str:
    """支持 ${VAR} / $VAR 插值（Hermes/OpenClaw 风格）。"""

    def repl(m: re.Match[str]) -> str:
        key = m.group(1) or m.group(2) or ""
        return os.environ.get(key, m.group(0))

    return _ENV_VAR.sub(repl, value)


def merge_env(
    existing: dict[str, Any] | None,
    incoming: dict[str, Any] | None,
    *,
    expand: bool = True,
) -> dict[str, str]:
    """合并 env：空值/脱敏不覆盖旧密钥。"""
    out: dict[str, str] = {}
    for k, v in (existing or {}).items():
        if v is None:
            continue
        vs = str(v)
        if not is_redacted_secret(vs):
            out[str(k)] = expand_env_value(vs) if expand else vs
    for k, v in (incoming or {}).items():
        if v is None:
            continue
        vs = str(v).strip()
        if is_redacted_secret(vs):
            # 保留 existing
            continue
        out[str(k)] = expand_env_value(vs) if expand else vs
    return out


def normalize_stdio_command_args(
    name: str,
    command: str | None,
    args: list[Any] | None,
) -> tuple[str | None, list[str]]:
    """纠正 command/args（尤其 tavily 错包）。"""
    cmd = (command or "").strip() or None
    out_args = [str(a) for a in (args or [])]

    # 扫描 args 中的错误包名
    fixed = False
    for _i, a in enumerate(out_args):
        key = a.strip()
        if key in _BAD_NPM_ARGS:
            # 替换整段 args 为推荐模板（保留 -y 风格）
            out_args = list(_BAD_NPM_ARGS[key])
            fixed = True
            break
        # 去掉 version 再匹配
        base = key.split("@")[0] if key.startswith("@") else key
        if base in _BAD_NPM_ARGS:
            out_args = list(_BAD_NPM_ARGS[base])
            fixed = True
            break

    # 按 server 名套默认（仅当 args 空或仍错误）
    key_name = (name or "").strip().lower()
    if key_name in _SERVER_DEFAULTS:
        d = _SERVER_DEFAULTS[key_name]
        joined = " ".join(out_args)
        if not out_args or fixed or any(
            x in joined
            for x in (
                "@tavily/mcp-server",
                "tavily-mcp-server",
                # 旧社区包 / 错误包名 → 官方 askecho
                "doubao-mcp",
                "huashu-doubao-search",
            )
        ):
            out_args = list(d["args"])
        if not cmd or cmd.lower() in ("npx", "npx.cmd", "uvx", "uvx.exe", "uv"):
            # 有默认 command 时优先采用（doubao 必须用 uvx，避免被 npx 占位）
            if key_name in (
                "doubao-search",
                "doubao",
                "askecho",
                "askecho-search",
                "mcp-server-askecho-search-infinity",
            ) or not cmd:
                cmd = str(d["command"])
            elif cmd.lower() in ("npx", "npx.cmd") and str(d["command"]) == "npx":
                cmd = str(d["command"])

    if sys.platform == "win32" and cmd and cmd.lower() in ("npx", "npm"):
        cmd = f"{cmd}.cmd"

    return cmd, out_args


def tool_name_allowed(
    remote_name: str,
    tools_include: list[str] | None,
    tools_exclude: list[str] | None,
) -> bool:
    """Hermes tools.include / exclude 过滤（原始 MCP 工具名，支持 fnmatch）。"""
    import fnmatch

    name = (remote_name or "").strip()
    if not name:
        return False
    include = [str(x).strip() for x in (tools_include or []) if str(x).strip()]
    exclude = [str(x).strip() for x in (tools_exclude or []) if str(x).strip()]
    if include:
        return any(fnmatch.fnmatch(name, pat) or name == pat for pat in include)
    if exclude:
        return not any(fnmatch.fnmatch(name, pat) or name == pat for pat in exclude)
    return True


def normalize_tool_name_list(raw: list[Any] | None) -> list[str] | None:
    """规范化白/黑名单；None 表示未设置，[] 表示显式空列表。"""
    if raw is None:
        return None
    out = [str(x).strip() for x in raw if str(x).strip()]
    # 去重保序
    seen: set[str] = set()
    uniq: list[str] = []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


def normalize_server_fields(
    *,
    name: str,
    command: str | None = None,
    args: list[Any] | None = None,
    env: dict[str, Any] | None = None,
    existing_env: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """供 manage_mcp / API 写路径统一调用。"""
    cmd, nargs = normalize_stdio_command_args(name, command, args)
    nenv = merge_env(existing_env, env)
    return {"command": cmd, "args": nargs, "env": nenv}
