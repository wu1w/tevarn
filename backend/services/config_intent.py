"""Config Intent 快路径：一句话配置不进 Agent 大 loop。

识别（轻量正则，无二次 LLM）：
- MCP 预制 + API Key
- 出站代理 host:port
- 切换模型
- 开启/关闭会话简单模式
- Grok/ChatGPT OAuth 引导（只给路径，不替用户浏览器登录）

命中则确定性执行 + 简短回复；未命中返回 None，交还 Agent。
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
        # 装下/安装 <名> MCP：KEY（预制或自定义；动词后紧贴名称，避免 .{0,n} 吞字）
        r"(?:装下|安装|装个|装上|添加)\s*"
        r"(?P<label>豆包(?:搜索)?|doubao(?:-?search)?|askecho|tavily|firecrawl|"
        r"[A-Za-z][\w\-]{1,40}|[\u4e00-\u9fff]{2,16})"
        r"(?:搜索|服务器)?"
        r"\s*(?:mcp|MCP)?\s*"
        r"[：:=\s]\s*[`\"']?(?P<key>[A-Za-z0-9_\-]{16,})[`\"']?",
        # 装下 xxx MCP KEY（空格分隔，无冒号）
        r"(?:装下|安装|装个|装上)\s*"
        r"(?P<label>豆包(?:搜索)?|doubao(?:-?search)?|tavily|firecrawl|[A-Za-z][\w\-]{1,40})"
        r"(?:搜索)?"
        r"\s*(?:mcp|MCP)\s+"
        r"[`\"']?(?P<key>[A-Za-z0-9_\-]{20,})[`\"']?",
        # 豆包 / doubao ... key ... VALUE
        r"(?P<label>豆包(?:搜索)?|doubao(?:-?search)?|askecho|tavily|firecrawl)"
        r".{0,40}?(?:api\s*key|密钥|key|token)"
        r"\s*[：:=\s]\s*[`\"']?(?P<key>[A-Za-z0-9_\-]{16,})[`\"']?",
        # 配下/装 api KEY（前有 豆包/mcp/搜索）
        r"(?:配|配置|设置|加上|写入|装下|安装|装个).{0,24}"
        r"(?P<label>豆包(?:搜索)?|doubao|tavily|firecrawl|mcp).{0,40}"
        r"[`\"']?(?P<key>[A-Za-z0-9_\-]{20,})[`\"']?",
        # 我给你加了…MCP，你配下 api KEY
        r"(?:mcp|搜索).{0,80}?(?:配|配置).{0,20}?(?:api|key|密钥)"
        r".{0,20}?[`\"']?(?P<key>[A-Za-z0-9_\-]{20,})[`\"']?",
    )
)

_PROXY_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.I)
    for p in (
        # 非贪婪间隔 + 完整 IPv4，避免 127.0.0.1 被拆成 host=1
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
    r"(?i)(开启|打开|启用|切换到|用)?\s*(简单模式|精简模式|simple\s*mode)",
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


def detect_config_intent(text: str) -> ConfigIntentMatch | None:
    t = (text or "").strip()
    if not t or len(t) > 2000:
        return None
    # 过长/多句任务：宁可交 Agent，避免误抢（软门禁）
    if len(t) > 400 and t.count("\n") >= 2:
        return None
    # 复杂任务：含「写代码/调试/分析」且无明确 key → 不抢
    if re.search(
        r"(?i)(写代码|实现功能|debug|traceback|重构|设计架构|分析一下|帮我查|搜索网页)", t
    ) and not re.search(r"(?i)(api\s*key|密钥|代理|oauth|简单模式|配下|配置)", t):
        return None

    # 简单模式开关（优先，短句）
    if _SIMPLE_OFF.search(t) and len(t) < 80:
        return ConfigIntentMatch("simple_mode", 0.95, {"enabled": False})
    if _SIMPLE_ON.search(t) and len(t) < 80 and not re.search(r"(?i)(不是|别|不要)", t):
        return ConfigIntentMatch("simple_mode", 0.9, {"enabled": True})

    # 代理：必须像配置句，且较短
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

    # MCP key：必须像「配/装 key」语境，避免误吞长文里的随机 token
    _mcp_ctx = bool(
        re.search(
            r"(?i)(api\s*key|密钥|配下|配置|写入|加上|装下|安装|装个|装上)"
            r".{0,40}(key|token|密钥|mcp)?",
            t,
        )
        or re.search(
            r"(?i)(豆包|doubao|tavily|firecrawl|askecho).{0,40}(key|密钥|token|mcp)",
            t,
        )
        or re.search(r"(?i)(装下|安装|装个|装上).{0,24}(mcp|豆包|doubao|tavily|firecrawl)", t)
    )
    if _mcp_ctx and len(t) < 600:
        for p in _MCP_KEY_PATTERNS:
            m = p.search(t)
            if not m:
                continue
            key = (m.groupdict().get("key") or "").strip()
            label = (m.groupdict().get("label") or "").strip().lower()
            if not key or len(key) < 16:
                continue
            # 若无 label，从全文猜
            if not label or label == "mcp":
                if re.search(r"豆包|doubao|askecho", t, re.I):
                    label = "doubao"
                elif re.search(r"tavily", t, re.I):
                    label = "tavily"
                elif re.search(r"firecrawl", t, re.I):
                    label = "firecrawl"
                else:
                    label = "doubao"  # 用户场景默认豆包
            return ConfigIntentMatch(
                "mcp_key",
                0.92,
                {"label": label, "api_key": key},
            )

        # 裸 key + 豆包语境（配/装/mcp 动词均可）
        if re.search(r"豆包|doubao", t, re.I) and re.search(
            r"(?i)(配|配置|api|装下|安装|装个|mcp)", t
        ):
            km = re.search(r"(?:[：:=\s]|mcp\s*)([A-Za-z0-9_\-]{28,})\b", t) or re.search(
                r"\b([A-Za-z0-9_\-]{28,})\b", t
            )
            if km:
                return ConfigIntentMatch(
                    "mcp_key",
                    0.85,
                    {"label": "doubao", "api_key": km.group(1)},
                )

    # OAuth 引导：短指令，避免聊天里顺口提到 login 就短路
    if len(t) < 80:
        for p in _OAUTH_PATTERNS:
            if p.search(t):
                kind = "oauth_xai" if re.search(r"(?i)grok|xai", t) else "oauth_openai"
                return ConfigIntentMatch(kind, 0.88, {})

    # 切模型（避免过宽：要求明确动词 + 短句）
    if len(t) < 120 and re.search(r"(?i)(切换|换成|改用|使用模型|模型\s*[：:=])", t):
        for p in _MODEL_PATTERNS:
            m = p.search(t)
            if m:
                return ConfigIntentMatch(
                    "switch_model",
                    0.86,
                    {"model": m.group("model").strip()},
                )

    return None


# ── 执行 ──────────────────────────────────────────────────────────

async def execute_config_intent(match: ConfigIntentMatch) -> dict[str, Any]:
    """执行意图，返回 {ok, message, ...}。"""
    t0 = time.time()
    kind = match.kind
    try:
        if kind == "mcp_key":
            result = await _exec_mcp_key(match.payload)
        elif kind == "proxy":
            result = await _exec_proxy(match.payload)
        elif kind == "switch_model":
            result = await _exec_switch_model(match.payload)
        elif kind == "simple_mode":
            result = {"ok": True, "message": "", "simple_mode": match.payload.get("enabled")}
            # session 写入在 shortcut 层做
        elif kind == "oauth_xai":
            result = {
                "ok": True,
                "message": (
                    "Grok OAuth 请走**设置 → LLM → Grok OAuth →「Grok 登录」**（设备码）。\n"
                    "若打不开验证页：先在 **设置 → 通用 → 网络代理** 填 HTTP 代理（如 127.0.0.1:3128）并保存。\n"
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


def _guess_mcp_env_key(existing_env: dict[str, Any], server_name: str) -> str:
    """为自定义 MCP 猜 env 键名：优先已有 *KEY*，否则 {NAME}_API_KEY。"""
    for k in existing_env:
        ku = str(k).upper()
        if "KEY" in ku or "TOKEN" in ku or "SECRET" in ku:
            return str(k)
    slug = re.sub(r"[^A-Za-z0-9]+", "_", (server_name or "MCP").upper()).strip("_") or "MCP"
    return f"{slug}_API_KEY"


async def _exec_mcp_custom_key(label: str, api_key: str) -> dict[str, Any]:
    """非预制：若 DB 已有同名/模糊 server 则写 key 并热同步；否则给出 manage_mcp 模板。"""
    from backend.repositories.mcp_server_repo import AsyncMCPServerRepository
    from backend.schemas.mcp import MCPServerUpdate
    from backend.services.secret_redact import mask_token

    repo = AsyncMCPServerRepository()
    label_l = label.strip().lower()
    obj = await repo.get_by_name(label.strip())
    if obj is None:
        for s in (await repo.list_all()) or []:
            sn = (s.name or "").lower()
            if not sn:
                continue
            if label_l == sn or label_l in sn or sn in label_l:
                obj = s
                break
    if obj is None:
        key_m = mask_token(api_key)
        return {
            "ok": False,
            "message": (
                f"「**{label}**」不是内置预制（预制快路径：豆包搜索 / tavily / firecrawl）。\n"
                "自定义 MCP 请用 **manage_mcp**（Agent 可执行）或设置页添加，例如：\n"
                f"- `action=add` `name={label or 'my-mcp'}` `transport=stdio`\n"
                "- `command` + `args`（如 uvx/npx 启动命令）\n"
                f"- `env` 写入密钥（已识别到 key 片段 `{key_m}`，请并入 env，勿写进 workspace 明文）\n"
                "添加成功后热同步；下一轮对话**点名服务名**即可 matching 挂载工具。\n"
                "若只要给**已存在**的自定义 server 补 key：先 `manage_mcp list`，再 "
                "`update` 的 `env`。"
            ),
            "detail": "unknown_preset_guide",
        }

    env = dict(obj.env or {})
    ek = _guess_mcp_env_key(env, obj.name or label)
    env[ek] = api_key
    updated = await repo.update(obj.id, MCPServerUpdate(env=env, enabled=True))
    runtime: dict[str, Any] = {}
    try:
        from backend.mcp_hub.service import sync_mcp_runtime

        runtime = await sync_mcp_runtime(only_server=updated.name if updated else obj.name)
    except Exception as e:
        runtime = {"ok": False, "error": str(e)}
    key_m = mask_token(api_key)
    return {
        "ok": True,
        "message": (
            f"已为**自定义** MCP `{updated.name if updated else obj.name}` 写入密钥"
            f"（env `{ek}`=`{key_m}`），并尝试热同步。\n"
            f"- runtime: {runtime.get('ok', runtime)}\n"
            "下一轮直接说「用 {name} …」即可；不必再 list 空转。"
        ).replace("{name}", str(updated.name if updated else obj.name)),
        "detail": "custom_env_updated",
        "data": {
            "server_name": (updated.name if updated else obj.name),
            "env_key": ek,
            "runtime": runtime,
        },
    }


async def _exec_mcp_key(payload: dict[str, Any]) -> dict[str, Any]:
    from backend.services.mcp_presets import ensure_mcp_preset, find_preset
    from backend.services.secret_redact import mask_token

    label = str(payload.get("label") or "doubao")
    api_key = str(payload.get("api_key") or "").strip()
    preset = find_preset(label)
    if preset is None:
        return await _exec_mcp_custom_key(label, api_key)
    out = await ensure_mcp_preset(preset=preset, api_key=api_key, reload=True, probe=True)
    key_m = mask_token(api_key)
    lines = [
        f"**{preset.display_name}** 已用快路径配置（未进入多步 Agent 循环）。",
        f"- Key：`{key_m}` → env `{preset.env_key}`",
        f"- 服务名：`{out.get('server_name')}`（{out.get('action')}）",
        f"- 启动：`{out.get('runner_note')}`",
        f"- 工具注册：{out.get('tools_registered', 0)} 个",
    ]
    if out.get("reload_error"):
        lines.append(f"- 刷新：有告警 `{out['reload_error'][:120]}`")
    if out.get("probe_ok") is True:
        lines.append(f"- 探活：成功（{out.get('probe_detail')}）")
    elif out.get("probe_ok") is False:
        lines.append(
            f"- 探活：未通过（{out.get('probe_detail')}）。"
            "配置已写入；可新开一轮对话再试搜索。"
        )
    else:
        lines.append("- 探活：跳过（无已注册工具）")
    lines.append("\n直接说「帮我搜一下 ……」即可。若工具仍不可见，**新开会话**以加载 MCP 工具表。")
    return {
        "ok": True,
        "message": "\n".join(lines),
        "detail": out.get("action"),
        "data": {k: v for k, v in out.items() if k != "ok"},
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
        return {"ok": True, "message": "已关闭 Tevarn/Takton 出站代理（立即生效）。", "detail": "off"}
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
    catalog["active_model"] = model  # 保留用户选择名
    # provider 级 active_model
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
    # 有附件时不抢（可能是分析文件）
    if attachments:
        return None
    # 子代理 / workforce 不抢
    if getattr(loop, "_parent_run_id", None) or getattr(loop, "_agent_key", None) not in (
        None,
        "",
        "main",
    ):
        ak = str(getattr(loop, "_agent_key", "") or "")
        if ak and ak != "main" and not ak.startswith("contact:"):
            return None

    match = detect_config_intent(user_input or "")
    if match is None:
        return None

    # simple_mode 需要 session
    if match.kind == "simple_mode":
        enabled = bool(match.payload.get("enabled"))
        try:
            if loop.session_repo is not None:
                cfg = await loop.session_repo.get_config(session_id) or {}
                if not isinstance(cfg, dict):
                    cfg = {}
                cfg["simple_mode"] = enabled
                if enabled:
                    cfg["simple_mode_max_iterations"] = 8
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

            record_intent_event(kind="simple_mode", ok=True, detail="on" if enabled else "off")
        except Exception:
            pass
        await _persist_shortcut(loop, session_id, user_input, reply)
        return reply

    result = await execute_config_intent(match)
    reply = str(result.get("message") or ("完成" if result.get("ok") else "失败"))
    await _persist_shortcut(loop, session_id, user_input, reply)
    return reply


async def _persist_shortcut(loop: Any, session_id: UUID, user_input: str, reply: str) -> None:
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
        # 广播完整 assistant 文本，便于前端刷新
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
