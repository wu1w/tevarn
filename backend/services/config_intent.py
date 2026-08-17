"""Config Intent 快路径：一句话配置不进 Agent 大 loop。

识别（轻量正则，无二次 LLM）：
- MCP 预制 + API Key（含 pending 二次贴 key）
- MCP 无 key → setup_guide（引导贴 key / 已装则 reload）
- 出站代理 host:port
- 切换模型
- 开启/关闭会话简单模式
- Grok/ChatGPT OAuth 引导

未命中完整快路径时，detect_mcp_micro_loop 可武装「配置微 loop」。
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)


@dataclass
class ConfigIntentMatch:
    kind: str
    confidence: float
    payload: dict[str, Any]


# ── 检测 ──────────────────────────────────────────────────────────

_MCP_KEY_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.I | re.S)
    for p in (
        r"(?P<label>豆包(?:搜索)?|doubao(?:-?search)?|askecho|tavily|firecrawl)"
        r".{0,40}?(?:api\s*key|密钥|key|token)"
        r"\s*[：:=\s]\s*[`\"']?(?P<key>[A-Za-z0-9_\-]{16,})[`\"']?",
        r"(?:配|配置|设置|加上|写入).{0,24}"
        r"(?P<label>豆包(?:搜索)?|doubao|tavily|firecrawl|mcp).{0,40}"
        r"[`\"']?(?P<key>[A-Za-z0-9_\-]{20,})[`\"']?",
        r"(?:mcp|搜索).{0,80}?(?:配|配置).{0,20}?(?:api|key|密钥)"
        r".{0,20}?[`\"']?(?P<key>[A-Za-z0-9_\-]{20,})[`\"']?",
        r"(?P<label>豆包(?:搜索)?|doubao(?:-?search)?|askecho|tavily|firecrawl)"
        r".{0,48}?(?:api\s*key|密钥|key|token)\s*[：:]\s*"
        r"[`\"']?(?P<key>[A-Za-z0-9_\-]{16,})[`\"']?",
    )
)

_MCP_SETUP_NO_KEY = re.compile(
    r"(?i)(?:"
    r"(?:帮我|请|麻烦)?(?:配\s*下|配\s*置|安装|挂载|启用|装\s*上|接\s*上).{0,24}"
    r"(?:豆包(?:搜索)?|doubao|tavily|firecrawl|askecho|mcp)|"
    r"(?:豆包(?:搜索)?|doubao|tavily|firecrawl|askecho).{0,16}"
    r"(?:mcp|搜索).{0,12}(?:配|装|配置|安装)?"
    r")"
)

_BARE_KEY = re.compile(r"^\s*[`\"']?([A-Za-z0-9_\-]{20,})[`\"']?\s*$")

_PROXY_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.I)
    for p in (
        r"(?:代理|proxy|出站代理).{0,16}?"
        r"(?P<host>127\.0\.0\.1|localhost|(?:\d{1,3}\.){3}\d{1,3}|[a-zA-Z][a-zA-Z0-9.-]{0,60})"
        r"\s*[:：]\s*(?P<port>\d{2,5})\b",
        r"(?:启用|开启|设置).{0,8}代理.{0,16}?"
        r"(?P<host>127\.0\.0\.1|localhost|(?:\d{1,3}\.){3}\d{1,3})"
        r"\s*[:：]\s*(?P<port>\d{2,5})\b",
        r"(?:关闭|停用|取消).{0,6}(?:代理|proxy)",
    )
)

_MODEL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.I)
    for p in (
        r"(?:切换|换成|改用|使用|用)\s*(?:模型\s*)?(?P<model>k3-256k|k3|kimi-for-coding(?:-highspeed)?|grok-4|gpt-5\.6|gpt-5|claude[^\s,]{0,20})",
        r"(?:模型)\s*[：:=]\s*(?P<model>[\w.+\-/]{2,40})",
    )
)

_SIMPLE_ON = re.compile(
    r"(?i)(开启|打开|启用|切换到|用)\s*(简单模式|精简模式|simple\s*mode)",
)
_SIMPLE_OFF = re.compile(
    r"(?i)(关闭|取消|退出)\s*(简单模式|精简模式|simple\s*mode)",
)

_OAUTH_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.I)
    for p in (
        r"(?:grok|xai).{0,12}(?:oauth|登录|login)",
        r"(?:chatgpt|openai).{0,12}(?:oauth|登录|login)",
        r"(?:oauth).{0,12}(?:grok|xai|chatgpt)",
    )
)


def _guess_mcp_label(
    text: str, explicit: str = "", *, default: str | None = None
) -> str | None:
    """解析预制 label。无产品信号时返回 default（默认 None，禁止静默豆包）。"""
    label = (explicit or "").strip().lower()
    if label and label not in {"mcp", "key", "api", "token"}:
        if re.search(r"豆包|doubao|askecho", label, re.I):
            return "doubao"
        if "tavily" in label:
            return "tavily"
        if "firecrawl" in label:
            return "firecrawl"
        if "fetch" in label:
            return "fetch"
        return label
    t = text or ""
    if re.search(r"豆包|doubao|askecho", t, re.I):
        return "doubao"
    if re.search(r"tavily", t, re.I):
        return "tavily"
    if re.search(r"firecrawl", t, re.I):
        return "firecrawl"
    if re.search(r"\bfetch\b|mcp-server-fetch", t, re.I):
        return "fetch"
    return default


def _extract_mcp_key_match(t: str) -> ConfigIntentMatch | None:
    """从文案抽出合法 key + preset；必须能解析到明确预制。"""
    for p in _MCP_KEY_PATTERNS:
        m = p.search(t)
        if not m:
            continue
        key = (m.groupdict().get("key") or "").strip()
        label_raw = (m.groupdict().get("label") or "").strip().lower()
        if not key or len(key) < 16:
            continue
        label = _guess_mcp_label(t, label_raw, default=None)
        if not label:
            continue
        return ConfigIntentMatch("mcp_key", 0.92, {"label": label, "api_key": key})

    if re.search(r"豆包|doubao|tavily|firecrawl", t, re.I) and re.search(
        r"(?i)(配|配置|api\s*key|密钥)", t
    ):
        km = re.search(r"\b([A-Za-z0-9_\-]{28,})\b", t)
        if km:
            label = _guess_mcp_label(t, default=None)
            if label:
                return ConfigIntentMatch(
                    "mcp_key", 0.85, {"label": label, "api_key": km.group(1)}
                )
    return None



_MCP_ADD_STDIO = re.compile(
    r"(?i)(?:添加|挂载|安装|接入|add|install|mount)\s*"
    r"(?:一个\s*)?(?:mcp|MCP)(?:\s*server)?\s*"
    r"(?:名叫|名称|name\s*[=：:]?\s*)?"
    r"[`\"']?(?P<name>[A-Za-z][\w.-]{1,48})[`\"']?\s*"
    r"(?:，|,|：|:|\s+)*"
    r"(?:用\s*)?(?P<command>npx|uvx|node|python3?|bun)\s+"
    r"(?P<args>.+)$"
)
_MCP_ADD_STDIO_LOOSE = re.compile(
    r"(?i)(?:添加|挂载|安装|add|install)\s+(?:mcp|MCP).{0,40}?"
    r"(?P<command>npx|uvx)\s+(?P<args>-y\s+\S+|\S+)"
)
_MCP_ADD_SSE = re.compile(
    r"(?i)(?:添加|挂载|安装|add)\s+(?:mcp|MCP).{0,40}?"
    r"(?:url|地址|endpoint)\s*[=：:]\s*"
    r"(?P<url>https?://\S+)"
)
_MCP_ADD_NAME_URL = re.compile(
    r"(?i)(?:mcp|MCP)\s+(?:sse\s+)?(?:name\s*[=：:]\s*)?"
    r"[`\"']?(?P<name>[A-Za-z][\w.-]{1,48})[`\"']?.{0,20}?"
    r"(?:url\s*[=：:]\s*)(?P<url>https?://\S+)"
)


def _split_mcp_args(raw: str) -> list[str]:
    s = (raw or "").strip().strip("`\"'")
    if not s:
        return []
    if s.startswith("["):
        try:
            import json
            arr = json.loads(s)
            if isinstance(arr, list):
                return [str(x) for x in arr]
        except Exception:
            pass
    parts: list[str] = []
    buf = ""
    in_q = ""
    for ch in s:
        if in_q:
            if ch == in_q:
                in_q = ""
            else:
                buf += ch
            continue
        if ch in "\"'":
            in_q = ch
            continue
        if ch.isspace():
            if buf:
                parts.append(buf)
                buf = ""
            continue
        buf += ch
    if buf:
        parts.append(buf)
    return parts


def _infer_remote_mcp_transport(url: str) -> str:
    """从 URL 推断远程 transport：/sse → sse，其余 http(s) 默认 streamable-http。"""
    u = (url or "").strip().lower()
    path = u
    try:
        from urllib.parse import urlparse

        path = (urlparse(u).path or "").lower()
    except Exception:
        pass
    if path.rstrip("/").endswith("/sse") or "/sse" in path or "transport=sse" in u:
        return "sse"
    # DeepWiki 等：/mcp 或裸 https URL → streamable-http
    return "streamable-http"


def _detect_mcp_add(t: str) -> ConfigIntentMatch | None:
    m = _MCP_ADD_SSE.search(t) or _MCP_ADD_NAME_URL.search(t)
    if m:
        gd = m.groupdict()
        url = (gd.get("url") or "").strip().rstrip(",.;")
        name = (gd.get("name") or "").strip()
        if not name:
            try:
                from urllib.parse import urlparse
                host = urlparse(url).hostname or "mcp-remote"
                name = re.sub(r"[^a-zA-Z0-9_-]", "-", host)[:32] or "mcp-remote"
            except Exception:
                name = "mcp-remote"
        if url.startswith("http"):
            transport = _infer_remote_mcp_transport(url)
            return ConfigIntentMatch(
                "mcp_add_custom", 0.9,
                {"name": name, "transport": transport, "url": url},
            )
    m = _MCP_ADD_STDIO.search(t)
    if m:
        gd = m.groupdict()
        name = (gd.get("name") or "").strip()
        command = (gd.get("command") or "npx").strip()
        args = _split_mcp_args(gd.get("args") or "")
        if name and args:
            return ConfigIntentMatch(
                "mcp_add_custom", 0.92,
                {"name": name, "transport": "stdio", "command": command, "args": args},
            )
    m = _MCP_ADD_STDIO_LOOSE.search(t)
    if m:
        command = (m.group("command") or "npx").strip()
        args = _split_mcp_args(m.group("args") or "")
        name = "mcp-custom"
        for a in args:
            if a.startswith("@") or "/" in a:
                name = re.sub(r"[^a-zA-Z0-9_-]", "-", a.split("/")[-1])[:40] or name
                break
        if args:
            return ConfigIntentMatch(
                "mcp_add_custom", 0.85,
                {"name": name, "transport": "stdio", "command": command, "args": args},
            )
    return None


def detect_config_intent(text: str) -> ConfigIntentMatch | None:
    t = (text or "").strip()
    if not t or len(t) > 2000:
        return None

    if _SIMPLE_OFF.search(t) and len(t) < 80:
        return ConfigIntentMatch("simple_mode", 0.95, {"enabled": False})
    if _SIMPLE_ON.search(t) and len(t) < 80 and not re.search(r"(?i)(不是|别|不要)", t):
        return ConfigIntentMatch("simple_mode", 0.9, {"enabled": True})

    if re.search(r"(?i)(关闭|停用|取消).{0,6}(代理|proxy)", t) and len(t) < 60:
        return ConfigIntentMatch("proxy", 0.92, {"enabled": False})
    if re.search(r"(?i)(代理|proxy)", t) and len(t) < 160:
        for p in _PROXY_PATTERNS:
            m = p.search(t)
            if m and m.groupdict().get("host") and m.groupdict().get("port"):
                return ConfigIntentMatch(
                    "proxy",
                    0.93,
                    {
                        "enabled": True,
                        "host": m.group("host"),
                        "port": int(m.group("port")),
                        "scheme": "http",
                    },
                )

    if re.search(
        r"(?i)(写代码|实现功能|debug|traceback|重构|设计架构|分析一下|帮我查|搜索网页)", t
    ) and not re.search(r"(?i)(api\s*key|密钥|代理|oauth|简单模式|配下|配置|mcp)", t):
        return None

    _mcp_ctx = bool(
        re.search(r"(?i)(api\s*key|密钥|配下|配置|写入|加上|mcp)", t)
        or re.search(r"(?i)(豆包|doubao|tavily|firecrawl|askecho)", t)
    )
    if _mcp_ctx:
        key_match = _extract_mcp_key_match(t)
        if key_match is not None:
            return key_match

    _long_multi = len(t) > 400 and t.count("\n") >= 2

    if not _long_multi and len(t) < 400 and _MCP_SETUP_NO_KEY.search(t):
        if not re.search(
            r"(?i)(搜\s*一下|搜索\s*一下|帮我\s*搜|search\s+for|查一下.{0,20}新闻)", t
        ):
            label = _guess_mcp_label(t, default=None)
            if label:
                return ConfigIntentMatch("mcp_setup_guide", 0.9, {"label": label})

    if len(t) < 80:
        for p in _OAUTH_PATTERNS:
            if p.search(t):
                kind = "oauth_xai" if re.search(r"(?i)grok|xai", t) else "oauth_openai"
                return ConfigIntentMatch(kind, 0.88, {})

    if len(t) < 120 and re.search(r"(?i)(切换|换成|改用|使用模型|模型\s*[：:=])", t):
        for p in _MODEL_PATTERNS:
            m = p.search(t)
            if m:
                return ConfigIntentMatch(
                    "switch_model", 0.86, {"model": m.group("model").strip()}
                )

    if len(t) < 500 and re.search(
        r"(?i)(添加|挂载|安装|接入|add|install|mount).{0,16}(mcp|MCP)", t
    ):
        add = _detect_mcp_add(t)
        if add is not None:
            return add

    return None


def try_pending_mcp_key(
    text: str,
    pending_label: str | None,
) -> ConfigIntentMatch | None:
    """上一轮引导后，用户只贴 key / API Key：xxx → mcp_key。"""
    t = (text or "").strip()
    if not t or not pending_label:
        return None
    label = _guess_mcp_label(pending_label, pending_label, default=None) or str(
        pending_label
    ).strip()
    if not label:
        return None
    m = _BARE_KEY.match(t)
    if m:
        key = m.group(1)
    else:
        m2 = re.search(
            r"(?i)(?:api\s*key|密钥|key)\s*[：:]\s*[`\"']?([A-Za-z0-9_\-]{16,})[`\"']?\s*$",
            t,
        )
        if m2 and len(t) < 160:
            key = m2.group(1)
        else:
            return None
    if len(key) < 16:
        return None
    return ConfigIntentMatch("mcp_key", 0.88, {"label": label, "api_key": key})


def detect_mcp_micro_loop(text: str) -> dict[str, Any] | None:
    """未命中完整快路径时，是否武装配置微 loop（薄工具面 + 短 max_iters）。

    F3: 「配一下/配置 … MCP」即使 is_mcp_ops_intent 漏检，也强制微 loop。
    """
    t = (text or "").strip()
    if not t:
        return None
    if detect_config_intent(t) is not None:
        return None
    # 明确在搜网页 → 不武装配置微 loop
    if re.search(
        r"(?i)(搜\s*一下|搜索\s*一下|帮我\s*搜|search\s+for|查一下.{0,20}新闻)", t
    ) and not re.search(r"(?i)(配|配置|安装|api\s*key|密钥)", t):
        return None
    ops = False
    try:
        from backend.agent.tool_policy import is_mcp_ops_intent

        ops = bool(is_mcp_ops_intent(t))
    except Exception:
        ops = False
    # F3 双保险：口语配/装 + mcp 标记
    if not ops:
        if re.search(
            r"(?i)(manage_mcp|mcp\s*商店|mcp\s*server|"
            r"配\s*一?\s*下.{0,24}mcp|配置.{0,12}mcp|装.{0,8}mcp|"
            r"mcp.{0,12}(配|装|配置|安装))",
            t,
        ):
            ops = True
    if not ops:
        return None
    label = _guess_mcp_label(t, default="") or ""
    try:
        from backend.core.config import settings as _st

        _mi = int(getattr(_st, "agent_config_micro_max_iterations", 5) or 5)
    except Exception:
        _mi = 5
    return {
        "label": label,
        "max_iters": max(2, _mi),
        "tools": (
            "manage_mcp",
            "clarify",
            "current_time",
            "update_config",
            "get_system_status",
            "list_available_models",
        ),
        "reason": "mcp_ops_without_full_key",
    }



async def execute_config_intent(match: ConfigIntentMatch) -> dict[str, Any]:
    t0 = time.time()
    kind = match.kind
    try:
        if kind == "mcp_key":
            result = await _exec_mcp_key(match.payload)
        elif kind == "mcp_setup_guide":
            result = await _exec_mcp_setup_guide(match.payload)
        elif kind == "mcp_add_custom":
            # S9：自定义 MCP 一句话安装（detect 已有，此前未挂接执行器）
            result = await _exec_mcp_add_custom(match.payload)
        elif kind == "proxy":
            result = await _exec_proxy(match.payload)
        elif kind == "switch_model":
            result = await _exec_switch_model(match.payload)
        elif kind == "simple_mode":
            result = {
                "ok": True,
                "message": "",
                "simple_mode": match.payload.get("enabled"),
            }
        elif kind == "oauth_xai":
            result = {
                "ok": True,
                "message": (
                    "Grok OAuth 请走**设置 → LLM → Grok OAuth →「Grok 登录」**（设备码）。\n"
                    "若打不开验证页：先在 **设置 → 通用 → 网络代理** 填 HTTP 代理并保存。\n"
                    "我不会在对话里替你打开浏览器完成授权。"
                ),
            }
        elif kind == "oauth_openai":
            result = {
                "ok": True,
                "message": (
                    "ChatGPT OAuth 请走**设置 → LLM → ChatGPT OAuth →「ChatGPT 登录」**。\n"
                    "若换 token 失败，请配置出站代理后重试。"
                ),
            }
        else:
            result = {"ok": False, "message": f"未知配置意图: {kind}"}
    except Exception as e:
        logger.warning("config intent exec failed kind=%s: %s", kind, e)
        result = {"ok": False, "message": f"配置执行失败: {e}"}

    ms = int((time.time() - t0) * 1000)
    try:
        from backend.services.intent_telemetry import record_intent_event

        record_intent_event(
            kind=kind,
            ok=bool(result.get("ok")),
            source="config_intent",
            detail=str(result.get("detail") or result.get("action") or "")[:80],
            duration_ms=ms,
        )
    except Exception:
        pass
    result["duration_ms"] = ms
    result["kind"] = kind
    return result


async def _exec_mcp_key(payload: dict[str, Any]) -> dict[str, Any]:
    from backend.services.mcp_presets import ensure_mcp_preset, find_preset
    from backend.services.secret_redact import mask_token

    label = str(payload.get("label") or "").strip()
    api_key = str(payload.get("api_key") or "").strip()
    if not label:
        return {
            "ok": False,
            "message": "未识别 MCP 预制名。请带上「豆包搜索 / tavily / firecrawl / deepwiki」。",
            "detail": "missing_label",
        }
    preset = find_preset(label)
    if preset is None:
        return {
            "ok": False,
            "message": (
                f"未识别的 MCP 预制「{label}」。"
                "支持：豆包搜索 / tavily / firecrawl / deepwiki / mcp-remote-deepwiki。"
            ),
            "detail": "unknown_preset",
        }
    out = await ensure_mcp_preset(preset=preset, api_key=api_key, reload=True, probe=True)
    key_m = mask_token(api_key)
    lines = [
        f"**{preset.display_name}** 已用快路径配置（未进入多步 Agent 循环）。",
    ]
    if preset.env_key:
        lines.append(f"- Key：`{key_m}` → env `{preset.env_key}`")
    lines.extend(
        [
            f"- 服务名：`{out.get('server_name')}`（{out.get('action')}）",
            f"- 传输：`{out.get('transport') or preset.transport}` · timeout={out.get('timeout') or preset.timeout}",
            f"- 启动：`{out.get('runner_note')}`",
            f"- 工具注册：{out.get('tools_registered', 0)} 个",
        ]
    )
    if out.get("reload_error") or out.get("connect_error"):
        err = out.get("connect_error") or out.get("reload_error")
        lines.append(f"- 连接：失败 `{str(err)[:160]}`")
        if out.get("conclude"):
            lines.append(
                "- 【自动收束】配置已写入；请停止对本 server 的重复安装/改配，"
                "向用户说明原因（网络/URL/timeout）。"
            )
    if out.get("probe_ok") is True:
        lines.append(f"- 探活：成功（{out.get('probe_detail')}）")
    elif out.get("probe_ok") is False:
        lines.append(
            f"- 探活：未通过（{out.get('probe_detail')}）。"
            "配置已写入；可新开一轮对话再试。"
        )
    elif out.get("ok"):
        lines.append("- 探活：跳过（无已注册工具）")
    if out.get("ok"):
        lines.append(
            "\n直接说「帮我搜一下 ……」或使用对应 mcp_* 工具即可。"
            "若工具仍不可见，**新开会话**以加载 MCP 工具表。"
        )
    wrote_ok = bool(out.get("ok", True))
    return {
        "ok": wrote_ok,
        "message": "\n".join(lines),
        "detail": out.get("action") if wrote_ok else "connect_failed",
        "data": {k: v for k, v in out.items() if k != "ok"},
        "clear_pending_mcp": True,
    }


async def _exec_mcp_setup_guide(payload: dict[str, Any]) -> dict[str, Any]:
    from backend.services.mcp_presets import ensure_mcp_preset, find_preset

    label = str(payload.get("label") or "").strip()
    if not label:
        return {
            "ok": False,
            "message": "请说明要配置哪个 MCP（豆包搜索 / tavily / firecrawl / deepwiki）。",
            "detail": "missing_label",
        }
    preset = find_preset(label)
    if preset is None:
        return {
            "ok": False,
            "message": (
                f"未识别的 MCP 预制「{label}」。"
                "支持：豆包搜索 / tavily / firecrawl / deepwiki / mcp-remote-deepwiki。"
            ),
            "detail": "unknown_preset",
        }

    try:
        from backend.repositories.mcp_server_repo import AsyncMCPServerRepository

        repo = AsyncMCPServerRepository()
        existing = None
        for s in await repo.list_all():
            n = str(getattr(s, "name", "") or "").lower()
            if preset.id in n or any(
                a.lower() in n for a in (preset.aliases or ()) if a
            ):
                existing = s
                break
            if preset.env_key and isinstance(getattr(s, "env", None), dict):
                if preset.env_key in (s.env or {}):
                    existing = s
                    break
    except Exception:
        existing = None

    if existing is not None:
        try:
            out = await ensure_mcp_preset(
                preset=preset, api_key="", reload=True, probe=True
            )
            return {
                "ok": True,
                "message": (
                    f"**{preset.display_name}** 已在配置中，已触发 reload"
                    f"（工具 {out.get('tools_registered', 0)} 个）。"
                    "直接说「帮我搜一下 ……」即可。"
                ),
                "detail": out.get("action") or "reloaded",
                "pending_mcp_label": None,
                "data": out,
            }
        except Exception as e:
            return {
                "ok": False,
                "message": f"已有配置但 reload 失败: {e}",
                "detail": "reload_failed",
            }

    if not preset.env_key:
        try:
            out = await ensure_mcp_preset(
                preset=preset, api_key="", reload=True, probe=True
            )
            return {
                "ok": True,
                "message": (
                    f"**{preset.display_name}** 无需 API Key，已写入并加载"
                    f"（工具 {out.get('tools_registered', 0)} 个）。"
                ),
                "detail": out.get("action") or "ensured_no_key",
                "pending_mcp_label": None,
                "data": out,
            }
        except Exception as e:
            return {
                "ok": False,
                "message": f"配置 {preset.display_name} 失败: {e}",
                "detail": "ensure_failed",
            }

    env_hint = f"env `{preset.env_key}`"
    msg = (
        f"要配置 **{preset.display_name}** MCP（{env_hint}）。\n"
        f"请**直接发**（一行即可秒配，无需再说明）：\n\n"
        f"`{preset.display_name} API Key：你的密钥`\n\n"
        "也可在 **设置 → MCP** 粘贴。我不会进多步工具循环去猜密钥。"
    )
    return {
        "ok": True,
        "message": msg,
        "detail": "awaiting_key",
        "pending_mcp_label": preset.id,
    }



async def _exec_mcp_add_custom(payload: dict[str, Any]) -> dict[str, Any]:
    """S9: 自定义 MCP 快路径 — 写库 + 热挂载（stdio/sse/streamable-http）。"""
    name = str(payload.get("name") or "").strip()
    transport_raw = str(payload.get("transport") or "stdio").strip().lower()
    command = str(payload.get("command") or "").strip() or None
    args = payload.get("args") or []
    if not isinstance(args, list):
        args = [str(args)]
    args = [str(a) for a in args if str(a).strip()]
    url = str(payload.get("url") or "").strip() or None
    env = payload.get("env") if isinstance(payload.get("env"), dict) else {}

    try:
        from backend.mcp_hub.normalize import (
            SUPPORTED_TRANSPORTS,
            is_url_transport,
            normalize_server_fields,
            normalize_transport,
        )
        from backend.repositories.mcp_server_repo import AsyncMCPServerRepository
        from backend.schemas.mcp import MCPServerCreate
    except Exception as e:
        return {"ok": False, "message": f"MCP 模块不可用: {e}", "detail": "import"}

    transport = normalize_transport(transport_raw) or transport_raw
    if url and (not transport or transport == "stdio"):
        transport = _infer_remote_mcp_transport(url)

    if not name:
        return {"ok": False, "message": "需要 MCP 名称（name）。", "detail": "missing_name"}
    if transport not in SUPPORTED_TRANSPORTS:
        return {
            "ok": False,
            "message": "transport 须为 stdio / sse / streamable-http。",
            "detail": "bad_transport",
        }
    if transport == "stdio" and not command:
        return {
            "ok": False,
            "message": "stdio 需要 command（如 npx / uvx）。",
            "detail": "missing_command",
        }
    if is_url_transport(transport) and not url:
        return {
            "ok": False,
            "message": f"{transport} 需要 url。",
            "detail": "missing_url",
        }

    timeout = 30.0
    joined = " ".join(args).lower()
    if is_url_transport(transport):
        timeout = 60.0
    if "mcp-remote" in joined or name.lower() in ("deepwiki", "mcp-remote"):
        timeout = 120.0

    repo = AsyncMCPServerRepository()
    try:
        existing = await repo.get_by_name(name)
        if existing is not None:
            return {
                "ok": False,
                "message": f"MCP Server `{name}` 已存在。可用 manage_mcp update 或换名。",
                "detail": "exists",
            }
    except Exception:
        pass

    try:
        norm = normalize_server_fields(
            name=name,
            command=command if transport == "stdio" else None,
            args=list(args) if transport == "stdio" else [],
            env=env or {},
            existing_env={},
        )
        data = MCPServerCreate(
            name=name,
            transport=transport,
            command=norm.get("command") if transport == "stdio" else None,
            args=list(norm.get("args") or []) if transport == "stdio" else [],
            url=url if is_url_transport(transport) else None,
            env=dict(norm.get("env") or env or {}),
            enabled=True,
            timeout=timeout,
            risk_level="low",
        )
        obj = await repo.create(data)
    except Exception as e:
        return {"ok": False, "message": f"写入 MCP 配置失败: {e}", "detail": "create_failed"}

    tools_n = 0
    rt_note = ""
    connected = False
    try:
        from backend.tools.builtins.manage_integration_tools import ManageMcp

        tool = ManageMcp()
        if hasattr(tool, "_sync_runtime"):
            rt = await tool._sync_runtime(only_server=name)
            if isinstance(rt, dict):
                tools_n = int(rt.get("registered") or 0)
                connected = name in (rt.get("connected") or []) and bool(rt.get("ok"))
                if connected:
                    rt_note = "connected"
                else:
                    rt_note = str(
                        rt.get("connect_error")
                        or rt.get("error")
                        or rt.get("warning")
                        or "not connected"
                    )
    except Exception as e:
        rt_note = str(e)[:160]

    lines = [
        f"**自定义 MCP `{name}`** 已用快路径写入（未进多步 Agent）。",
        f"- transport：`{transport}` · timeout={timeout:.0f}s",
    ]
    if transport == "stdio":
        lines.append(f"- 启动：`{command} {' '.join(args)}`".rstrip())
    else:
        lines.append(f"- url：`{url}`")
    if connected:
        if tools_n:
            lines.append(f"- 热挂载：已注册约 {tools_n} 个工具")
        lines.append(f"需要密钥时直接说：`{name} API Key：xxxx` 或设置 → MCP。")
        return {
            "ok": True,
            "message": "\n".join(lines),
            "detail": "added",
            "data": {
                "name": name,
                "transport": transport,
                "tools_registered": tools_n,
                "timeout": timeout,
            },
        }

    lines.append(f"- 运行时：连接失败 — {rt_note[:200]}")
    lines.append(
        "【自动收束】配置已写入 DB；请停止对本 server 的 add/update 重试，"
        "向用户说明原因（网络/URL/timeout/transport）。"
        "DeepWiki 类可用 streamable-http + 官方 /mcp URL，"
        "或 stdio + mcp-remote（timeout≥120）。"
    )
    return {
        "ok": False,
        "message": "\n".join(lines),
        "detail": "connect_failed",
        "data": {
            "name": name,
            "transport": transport,
            "tools_registered": tools_n,
            "db_written": True,
            "conclude": True,
            "runtime_error": rt_note[:300],
            "timeout": timeout,
            "server_id": str(getattr(obj, "id", "") or ""),
        },
    }


async def _exec_proxy(payload: dict[str, Any]) -> dict[str, Any]:
    from backend.core.outbound_http import (
        build_proxy_url_from_parts,
        sync_proxy_env_from_settings,
    )
    from backend.core.runtime_settings import apply_settings_dict
    from backend.repositories.setting_repo import AsyncSettingRepository

    repo = AsyncSettingRepository()
    enabled = bool(payload.get("enabled"))
    host = str(payload.get("host") or "").strip()
    port = int(payload.get("port") or 0)
    scheme = str(payload.get("scheme") or "http")
    items = {
        "outbound_proxy_enabled": enabled,
        "outbound_proxy_scheme": scheme if enabled else "http",
        "outbound_proxy_host": host if enabled else "",
        "outbound_proxy_port": port if enabled else 0,
    }
    for k, v in items.items():
        await repo.upsert(k, v, "network")
    apply_settings_dict(items, reset=False)
    resolved = sync_proxy_env_from_settings()
    built = build_proxy_url_from_parts(
        enabled=enabled, host=host, port=port, scheme=scheme
    )
    if not enabled:
        return {
            "ok": True,
            "message": "已关闭 Tevarn 出站代理（立即生效）。",
            "detail": "off",
        }
    return {
        "ok": True,
        "message": (
            f"出站代理已启用：`{built or resolved}`（立即生效，无需重启）。\n"
            "可用于 Grok/ChatGPT OAuth 与模型 API。设置页 → 通用 → 网络代理 可改。"
        ),
        "detail": "on",
    }


async def _exec_switch_model(payload: dict[str, Any]) -> dict[str, Any]:
    from backend.core import model_catalog as model_catalog_mod
    from backend.core.runtime_settings import apply_settings_dict
    from backend.repositories.setting_repo import AsyncSettingRepository
    from backend.services.llm.openai_compatible import OpenAICompatibleService

    model = str(payload.get("model") or "").strip()
    if not model:
        return {"ok": False, "message": "未解析到模型名"}
    repo = AsyncSettingRepository()
    catalog = await model_catalog_mod.load_catalog(repo)
    base_url = ""
    pid = str(catalog.get("active_provider_id") or "")
    for p in catalog.get("providers") or []:
        if p.get("id") == pid:
            base_url = str(p.get("llm_base_url") or "")
            break
    if not base_url:
        from backend.core.config import settings

        base_url = str(getattr(settings, "llm_base_url", "") or "")

    effective = OpenAICompatibleService._normalize_model_id(model, base_url)
    catalog["active_model"] = model
    for p in catalog.get("providers") or []:
        if p.get("id") == pid:
            p["active_model"] = model
            cached = list(p.get("cached_models") or [])
            if model not in cached:
                cached.insert(0, model)
            p["cached_models"] = cached
            break
    await model_catalog_mod.save_catalog(repo, catalog)
    await repo.upsert("llm_model", model, "llm")
    apply_settings_dict({"llm_model": model}, reset=True)

    note = ""
    if effective != model:
        note = f"\n- 上游实际请求名：`{effective}`（供应商映射，属正常）"
    return {
        "ok": True,
        "message": (
            f"已切换模型为 **{model}**（供应商 `{pid or 'current'}`）。{note}\n"
            "新对话将使用该模型；可在设置 → LLM 确认「选用 / 实际上游」。"
        ),
        "detail": effective,
        "selected_model": model,
        "effective_model": effective,
    }


async def try_config_intent_shortcut(
    loop: Any,
    session_id: UUID,
    user_input: str,
    attachments: list | None = None,
) -> str | None:
    """Agent 入口短路。命中返回回复文本，否则 None。"""
    if attachments:
        return None
    if getattr(loop, "_parent_run_id", None) or getattr(loop, "_agent_key", None) not in (
        None,
        "",
        "main",
    ):
        ak = str(getattr(loop, "_agent_key", "") or "")
        if ak and ak != "main" and not ak.startswith("contact:"):
            return None

    pending_label: str | None = None
    try:
        if loop.session_repo is not None:
            cfg = await loop.session_repo.get_config(session_id) or {}
            if isinstance(cfg, dict):
                pending_label = str(cfg.get("pending_mcp_label") or "").strip() or None
    except Exception:
        pending_label = None

    match: ConfigIntentMatch | None = None
    if pending_label:
        match = try_pending_mcp_key(user_input or "", pending_label)
    if match is None:
        match = detect_config_intent(user_input or "")
    if match is None:
        return None

    if match.kind == "simple_mode":
        enabled = bool(match.payload.get("enabled"))
        try:
            if loop.session_repo is not None:
                updates: dict[str, Any] = {"simple_mode": enabled}
                if enabled:
                    updates["simple_mode_max_iterations"] = 8
                if hasattr(loop.session_repo, "merge_config_keys"):
                    await loop.session_repo.merge_config_keys(session_id, updates)
                else:
                    cfg = await loop.session_repo.get_config(session_id) or {}
                    if not isinstance(cfg, dict):
                        cfg = {}
                    cfg.update(updates)
                    await loop.session_repo.update(session_id, {"config": cfg})
        except Exception as e:
            logger.warning("simple_mode session update failed: %s", e)
            reply = f"设置简单模式失败: {e}"
            await _persist_shortcut(loop, session_id, user_input, reply)
            return reply
        reply = (
            "已**开启简单模式**（本会话）：工具更少、更倾向确认、默认短循环（约 8 轮）。"
            "说「关闭简单模式」可恢复。"
            if enabled
            else "已**关闭简单模式**，本会话恢复默认工具与循环深度。"
        )
        try:
            from backend.services.intent_telemetry import record_intent_event

            record_intent_event(
                kind="simple_mode", ok=True, detail="on" if enabled else "off"
            )
        except Exception:
            pass
        await _persist_shortcut(loop, session_id, user_input, reply)
        return reply

    result = await execute_config_intent(match)
    reply = str(result.get("message") or ("完成" if result.get("ok") else "失败"))

    try:
        if loop.session_repo is not None:
            updates: dict[str, Any] = {}
            remove: list[str] = []
            if (
                match.kind == "mcp_key"
                and result.get("ok")
                and result.get("clear_pending_mcp")
            ):
                remove.append("pending_mcp_label")
            elif result.get("pending_mcp_label"):
                updates["pending_mcp_label"] = str(result["pending_mcp_label"])
            elif (
                result.get("pending_mcp_label") is None
                and "pending_mcp_label" in result
                and result.get("ok")
            ):
                remove.append("pending_mcp_label")
            if updates or remove:
                if hasattr(loop.session_repo, "merge_config_keys"):
                    await loop.session_repo.merge_config_keys(
                        session_id, updates or None, remove=remove or None
                    )
                else:
                    cfg = await loop.session_repo.get_config(session_id) or {}
                    if not isinstance(cfg, dict):
                        cfg = {}
                    cfg.update(updates)
                    for k in remove:
                        cfg.pop(k, None)
                    await loop.session_repo.update(session_id, {"config": cfg})
    except Exception as e:
        logger.warning("pending_mcp_label session update failed: %s", e)

    await _persist_shortcut(loop, session_id, user_input, reply)
    return reply


async def _persist_shortcut(
    loop: Any, session_id: UUID, user_input: str, reply: str
) -> None:
    from backend.services.secret_redact import redact_secrets

    safe_user = redact_secrets(user_input)
    safe_reply = redact_secrets(reply)
    try:
        await loop._persist_user_input(session_id, safe_user, display_content=safe_user)
    except Exception as e:
        logger.warning("config intent persist user failed: %s", e)
    try:
        await loop._persist_final_response(session_id, safe_reply)
    except Exception as e:
        logger.warning("config intent persist assistant failed: %s", e)
    try:
        await loop._push_status(session_id, "idle", "配置快路径完成")
        ws = getattr(loop, "ws_manager", None)
        if ws is not None:
            await ws.broadcast(
                session_id,
                {
                    "type": "message",
                    "role": "assistant",
                    "content": safe_reply,
                    "source": "config_intent",
                },
            )
    except Exception:
        pass
