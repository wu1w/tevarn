"""
Settings 路由
运行时配置管理 API —— 对标 Hermes/OpenClaw 的「选服务商 → 填 Key → 能聊天」路径
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from backend.api.websocket import manager as ws_manager
from backend.core import model_catalog as model_catalog_mod
from backend.core.audit import AuditAction, log_action
from backend.core.encryption import decrypt_setting
from backend.core.runtime_settings import (
    apply_setting_value,
    apply_settings_dict,
    reset_factories_for_keys,
)
from backend.repositories import SettingRepository
from backend.schemas.setting import SettingRead, SettingUpdate
from backend.schemas.user import UserRead

from ..dependencies import (
    get_current_user,
    get_session_repo,
    get_setting_repo,
    require_admin,
)

logger = logging.getLogger(__name__)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["Settings"])

def _notify_settings_changed(user_id: uuid.UUID, keys: list[str]) -> None:
    """通知同一用户的所有连接：配置已变更，建议刷新模型目录等。"""
    try:
        import asyncio
        asyncio.create_task(
            ws_manager.broadcast_to_user(
                user_id,
                {"type": "settings_changed", "keys": keys, "ts": datetime.now(timezone.utc).isoformat()},
            )
        )
    except Exception:
        pass


# ---- 零基础预设（前端卡片数据源；也可供 API 消费者使用）----

PROVIDER_PRESETS: list[dict[str, Any]] = [
    {
        "id": "ollama",
        "name": "本地运行",
        "badge": "免费推荐",
        "description": "用 Ollama 在本机跑模型，数据不出电脑，适合新手体验。",
        "icon": "💻",
        "needs_api_key": False,
        "help_url": "https://ollama.com",
        "help_text": "安装 Ollama 后，在终端执行 ollama pull llama3.2",
        "llm": {
            "llm_provider": "ollama",
            "llm_base_url": "http://localhost:11434",
            "llm_model": "llama3.2",
            "llm_api_key": "",
        },
        "models": ["llama3.2", "llama3.1", "qwen2.5", "deepseek-r1", "mistral", "gemma2"],
        "embedding": {
            "embedding_provider": "ollama",
            "embedding_base_url": "http://localhost:11434",
            "embedding_model": "nomic-embed-text",
            "embedding_api_key": "",
        },
        "supports_multi_key": False,
    },
    {
        "id": "openai",
        "name": "OpenAI",
        "badge": "官方",
        "description": "GPT 系列官方接口。在 platform.openai.com 创建 API Key。",
        "icon": "🟢",
        "needs_api_key": True,
        "help_url": "https://platform.openai.com/api-keys",
        "help_text": "把 sk- 开头的密钥粘贴到下方；可添加多个 Key 轮换",
        "llm": {
            "llm_provider": "openai",
            "llm_base_url": "https://api.openai.com",
            "llm_model": "",
            "llm_api_key": "",
        },
        "models": [],
        "embedding": {
            "embedding_provider": "openai",
            "embedding_base_url": "https://api.openai.com",
            "embedding_model": "text-embedding-3-small",
            "embedding_api_key": "",
        },
        "supports_multi_key": True,
    },
    {
        "id": "openai-chatgpt-oauth",
        "name": "ChatGPT 会员 (OAuth)",
        "badge": "订阅登录",
        "description": (
            "用 ChatGPT Plus/Pro 浏览器登录（Codex OAuth），走订阅额度，无需 sk- API Key。"
            "受公平使用限制；适合 Codex/GPT 编码模型。"
        ),
        "icon": "💠",
        "needs_api_key": False,
        "auth_mode": "oauth_pkce",
        "oauth_provider": "openai",
        "help_url": "https://chatgpt.com/",
        "help_text": (
            "点击「ChatGPT 登录」→ 浏览器授权 → 复制跳转后的 localhost URL（含 code=）粘贴回来。"
            "与 platform API Key 计费相互独立。"
        ),
        "llm": {
            "llm_provider": "openai-compatible",
            "llm_base_url": "http://127.0.0.1:8090/api/llm-proxy/openai-codex/v1",
            "llm_model": "gpt-5.6",
            "llm_api_key": "",
        },
        "models": [
            # GPT-5.6 系列（Codex / ChatGPT 订阅；Sol=旗舰，Terra=均衡，Luna=快速）
            "gpt-5.6",
            "gpt-5.6-sol",
            "gpt-5.6-terra",
            "gpt-5.6-luna",
            # 上一代（部分账号仍可用）
            "gpt-5.4",
            "gpt-5.3-codex",
            "gpt-5.2-codex",
            "gpt-5.1-codex",
            "o3",
            "o4-mini",
            "gpt-4.1",
            "gpt-4o",
        ],
        "embedding": None,
        "supports_multi_key": False,
    },
    {
        "id": "anthropic",
        "name": "Claude",
        "badge": "Anthropic",
        "description": "Claude 官方接口，擅长长文与编程。",
        "icon": "🟣",
        "needs_api_key": True,
        "help_url": "https://console.anthropic.com/settings/keys",
        "help_text": "在 Anthropic Console 创建 API Key；支持多个 Key",
        "llm": {
            "llm_provider": "anthropic",
            "llm_base_url": "https://api.anthropic.com",
            "llm_model": "claude-sonnet-4-20250514",
            "llm_api_key": "",
        },
        "models": [],
        "embedding": None,
        "supports_multi_key": True,
    },
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "badge": "高性价比",
        "description": "国产高性价比模型，中文表现好，兼容 OpenAI 格式；多轮相同前缀自动磁盘缓存降本。",
        "icon": "🔵",
        "needs_api_key": True,
        "help_url": "https://platform.deepseek.com/api_keys",
        "help_text": "在 DeepSeek 开放平台创建 API Key；reasoner/R1 输出 token 多，长任务建议 chat 模型",
        "llm": {
            "llm_provider": "openai-compatible",
            "llm_base_url": "https://api.deepseek.com",
            "llm_model": "deepseek-chat",
            "llm_api_key": "",
        },
        "models": [],
        "embedding": None,
        "supports_multi_key": True,
    },
    {
        "id": "qwen",
        "name": "通义千问",
        "badge": "阿里云",
        "description": "阿里云 DashScope，兼容 OpenAI 协议；支持隐式/显式上下文缓存。",
        "icon": "🟠",
        "needs_api_key": True,
        "help_url": "https://dashscope.console.aliyun.com/apiKey",
        "help_text": "在阿里云百炼 / DashScope 创建 API Key；稳定 system+tools 前缀可提升 cache 命中",
        "llm": {
            "llm_provider": "openai-compatible",
            "llm_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "llm_model": "qwen-plus",
            "llm_api_key": "",
        },
        "models": [],
        "embedding": {
            "embedding_provider": "openai-compatible",
            "embedding_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "embedding_model": "text-embedding-v3",
            "embedding_api_key": "",
        },
        "supports_multi_key": True,
    },
    {
        "id": "kimi-plan",
        "name": "Kimi Plan",
        "badge": "编程套餐",
        "description": "Kimi 编程 / Coding Plan 专用端点（api.kimi.com），与开放平台 Moonshot API 分开。",
        "icon": "🌙",
        "needs_api_key": True,
        "help_url": "https://www.kimi.com/code",
        "help_text": "使用 Kimi Code / Plan 的 API Key；端点为 coding 专用",
        "llm": {
            "llm_provider": "openai-compatible",
            "llm_base_url": "https://api.kimi.com/coding/v1",
            "llm_model": "kimi-k2.5",
            "llm_api_key": "",
        },
        "models": [],
        "embedding": None,
        "supports_multi_key": True,
    },
    {
        "id": "moonshot",
        "name": "Moonshot API",
        "badge": "开放平台",
        "description": "月之暗面开放平台通用 API（api.moonshot.cn），按量计费，与 Kimi Plan 端点不同。",
        "icon": "🌑",
        "needs_api_key": True,
        "help_url": "https://platform.moonshot.cn/console/api-keys",
        "help_text": "在 Moonshot 开放平台创建 API Key（国内）",
        "llm": {
            "llm_provider": "openai-compatible",
            "llm_base_url": "https://api.moonshot.cn/v1",
            "llm_model": "moonshot-v1-auto",
            "llm_api_key": "",
        },
        "models": [],
        "embedding": None,
        "supports_multi_key": True,
    },
    {
        "id": "zhipu",
        "name": "智谱 GLM",
        "badge": "智谱",
        "description": "智谱清言 GLM 系列，兼容 OpenAI 协议；重复前缀自动上下文缓存。",
        "icon": "🔷",
        "needs_api_key": True,
        "help_url": "https://open.bigmodel.cn/usercenter/apikeys",
        "help_text": "在智谱开放平台创建 API Key；glm-4-flash 适合摘要/子任务",
        "llm": {
            "llm_provider": "openai-compatible",
            "llm_base_url": "https://open.bigmodel.cn/api/paas/v4",
            "llm_model": "glm-4-flash",
            "llm_api_key": "",
        },
        "models": [],
        "embedding": {
            "embedding_provider": "openai-compatible",
            "embedding_base_url": "https://open.bigmodel.cn/api/paas/v4",
            "embedding_model": "embedding-3",
            "embedding_api_key": "",
        },
        "supports_multi_key": True,
    },
    {
        "id": "xfyun-astron",
        "name": "讯飞星辰",
        "badge": "讯飞 MaaS",
        "description": "科大讯飞星辰 MaaS，OpenAI 兼容协议。",
        "icon": "⭐",
        "needs_api_key": True,
        "help_url": "https://maas.xfyun.cn/",
        "help_text": "在讯飞星辰 MaaS 控制台创建 API Key",
        "llm": {
            "llm_provider": "openai-compatible",
            "llm_base_url": "https://maas-coding-api.cn-huabei-1.xf-yun.com/v2",
            "llm_model": "xopglm51",
            "llm_api_key": "",
        },
        "models": [
            "xopglm51", "xopglm52", "xopglm5", "xopglmv47flash",
            "xopkimik26", "xopkimik25", "xminimaxm25",
            "xopdeepseekv4flash", "xopdeepseekv32", "xopdeepseekv4pro",
            "xopqwen35397b", "xopqwen36v35b", "xop3qwencodernext",
            "xsparkx2", "xsparkx2flash", "xsparkx2agent", "auto",
        ],
        "embedding": None,
        "supports_multi_key": True,
    },
    {
        "id": "volcengine-ark",
        "name": "火山引擎",
        "badge": "方舟",
        "description": "火山引擎方舟（豆包等），OpenAI 兼容。Coding Plan 可用 /api/coding/v3。",
        "icon": "🌋",
        "needs_api_key": True,
        "help_url": "https://console.volcengine.com/ark",
        "help_text": "在火山方舟控制台创建 API Key；模型 ID 多为推理接入点 ID",
        "llm": {
            "llm_provider": "openai-compatible",
            "llm_base_url": "https://ark.cn-beijing.volces.com/api/v3",
            "llm_model": "doubao-pro-32k",
            "llm_api_key": "",
        },
        "models": [],
        "embedding": None,
        "supports_multi_key": True,
    },
    {
        "id": "minimax",
        "name": "MiniMax",
        "badge": "国际",
        "description": "MiniMax 国际站 OpenAI 兼容接口。稳定 system/tools 前缀可触发 prompt cache 降本。",
        "icon": "🟣",
        "needs_api_key": True,
        "help_url": "https://platform.minimax.io/",
        "help_text": "在 MiniMax 国际平台创建 API Key；长对话请保持 system 与工具列表稳定以提升缓存命中",
        "llm": {
            "llm_provider": "openai-compatible",
            "llm_base_url": "https://api.minimax.io/v1",
            "llm_model": "MiniMax-M2.5",
            "llm_api_key": "",
        },
        "models": [],
        "embedding": None,
        "supports_multi_key": True,
    },
    {
        "id": "mimo",
        "name": "小米 MiMo",
        "badge": "长上下文",
        "description": "小米 MiMo OpenAI 兼容接口，适合长上下文与 Agent；前缀缓存命中可显著降本。",
        "icon": "📱",
        "needs_api_key": True,
        "help_url": "https://platform.xiaomimimo.com/",
        "help_text": "在小米 MiMo 开放平台创建 API Key；默认大窗口，稳定前缀可提升 cache hit",
        "llm": {
            "llm_provider": "openai-compatible",
            "llm_base_url": "https://api.xiaomimimo.com/v1",
            "llm_model": "mimo-v2.5",
            "llm_api_key": "",
        },
        "models": [],
        "embedding": None,
        "supports_multi_key": True,
    },
    {
        "id": "minimax-cn",
        "name": "MiniMax 国内",
        "badge": "中国站",
        "description": "MiniMax 国内站（minimaxi.com）OpenAI 兼容接口。",
        "icon": "🟪",
        "needs_api_key": True,
        "help_url": "https://platform.minimaxi.com/",
        "help_text": "在 MiniMax 国内开放平台创建 API Key",
        "llm": {
            "llm_provider": "openai-compatible",
            "llm_base_url": "https://api.minimaxi.com/v1",
            "llm_model": "MiniMax-M2.5",
            "llm_api_key": "",
        },
        "models": [],
        "embedding": None,
        "supports_multi_key": True,
    },
    {
        "id": "opencode-zen",
        "name": "OpenCode Zen",
        "badge": "精选模型",
        "description": "OpenCode 官方精选模型网关，按量使用。",
        "icon": "🧘",
        "needs_api_key": True,
        "help_url": "https://opencode.ai/zen",
        "help_text": "在 opencode.ai/auth 登录后复制 Zen API Key",
        "llm": {
            "llm_provider": "openai-compatible",
            "llm_base_url": "https://opencode.ai/zen/v1",
            "llm_model": "gpt-5.4",
            "llm_api_key": "",
        },
        "models": [],
        "embedding": None,
        "supports_multi_key": True,
    },
    {
        "id": "opencode-go",
        "name": "OpenCode Go",
        "badge": "订阅",
        "description": "OpenCode Go 订阅套餐，OpenAI 兼容。",
        "icon": "🚀",
        "needs_api_key": True,
        "help_url": "https://opencode.ai/docs/go/",
        "help_text": "订阅 OpenCode Go 后复制 API Key",
        "llm": {
            "llm_provider": "openai-compatible",
            "llm_base_url": "https://opencode.ai/zen/go/v1",
            "llm_model": "default",
            "llm_api_key": "",
        },
        "models": [],
        "embedding": None,
        "supports_multi_key": True,
    },
    {
        "id": "openrouter",
        "name": "OpenRouter",
        "badge": "多模型",
        "description": "一个 Key 调用多家模型（Claude / GPT / Gemini 等）。",
        "icon": "🌐",
        "needs_api_key": True,
        "help_url": "https://openrouter.ai/keys",
        "help_text": "在 OpenRouter 创建 API Key；同一供应商可添加多个 Key",
        "llm": {
            "llm_provider": "openai-compatible",
            "llm_base_url": "https://openrouter.ai/api/v1",
            "llm_model": "",
            "llm_api_key": "",
        },
        "models": [],
        "embedding": None,
        "supports_multi_key": True,
    },
    {
        "id": "xai",
        "name": "xAI Grok",
        "badge": "API Key",
        "description": "xAI 官方 API（api.x.ai），使用控制台创建的 API Key 按量计费。",
        "icon": "𝕏",
        "needs_api_key": True,
        "help_url": "https://console.x.ai/team/default/api-keys",
        "help_text": "在 console.x.ai 创建 API Key（以 xai- 开头）",
        "llm": {
            "llm_provider": "openai-compatible",
            "llm_base_url": "https://api.x.ai/v1",
            "llm_model": "grok-4",
            "llm_api_key": "",
        },
        "models": [],
        "embedding": None,
        "supports_multi_key": True,
    },
    {
        "id": "xai-oauth",
        "name": "Grok OAuth",
        "badge": "订阅登录",
        "description": "用 SuperGrok / X Premium+ 浏览器登录（设备码 OAuth），无需 API Key。对齐 Hermes 的 xAI Grok OAuth。",
        "icon": "⚡",
        "needs_api_key": False,
        "auth_mode": "oauth_device_code",
        "oauth_provider": "xai",
        "help_url": "https://x.ai/grok",
        "help_text": "点击「Grok 登录」→ 浏览器打开 accounts.x.ai → 输入验证码完成授权。若推理 403 请改用 xAI API Key。",
        "llm": {
            "llm_provider": "openai-compatible",
            "llm_base_url": "https://api.x.ai/v1",
            "llm_model": "grok-4",
            "llm_api_key": "",
        },
        "models": [],
        "embedding": None,
        "supports_multi_key": False,
    },
    {
        "id": "custom",
        "name": "自定义接口",
        "badge": "高级",
        "description": "任意 OpenAI 兼容服务（vLLM、LM Studio、OneAPI、代理等）。",
        "icon": "⚙️",
        "needs_api_key": False,
        "help_url": "",
        "help_text": "填写服务地址（例如 http://127.0.0.1:1234/v1）和模型名；可添加多个 Key",
        "llm": {
            "llm_provider": "openai-compatible",
            "llm_base_url": "http://127.0.0.1:1234/v1",
            "llm_model": "default",
            "llm_api_key": "",
        },
        "models": [],
        "embedding": None,
        "custom": True,
        "supports_multi_key": True,
    },
]


class BatchSettingsBody(BaseModel):
    """批量写入配置（一键应用预设）"""

    items: dict[str, Any] = Field(..., description="key → value 映射")
    sync_embedding: bool = Field(
        True,
        description="若包含 llm_* 且预设带 embedding，是否一并写入（由前端决定最终 items）",
    )


class TestLLMBody(BaseModel):
    """连通性测试 / 拉取模型列表：可临时覆盖，不落库"""

    llm_provider: Optional[str] = None
    llm_base_url: Optional[str] = None
    llm_model: Optional[str] = None
    llm_api_key: Optional[str] = None
    # 已登记供应商 id：拉取成功后写回该条 cached_models（优先于 base_url 模糊匹配）
    provider_id: Optional[str] = None


def _models_url(base_url: str) -> str:
    base = (base_url or "").rstrip("/")
    # 已带 API 版本号后缀（/v1 /v2 /v3 /v4 /api 等）直接拼 /models，
    # 否则默认补 /v1/models。修复讯飞 /v2 端点被拼成 /v2/v1/models → 301 → 403。
    import re as _re
    if _re.search(r"/(v\d+|api)$", base):
        return f"{base}/models"
    return f"{base}/v1/models"


def _sort_model_ids(ids: list[str]) -> list[str]:
    """聊天类模型靠前，embedding/tts 等靠后；不做假数据过滤丢弃。"""
    deprioritize = (
        "embed", "whisper", "tts", "dall-e", "davinci", "moderation",
        "image", "audio", "transcri", "babbage", "ada-", "text-similarity",
    )
    prioritize = (
        "gpt", "claude", "deepseek", "qwen", "llama", "gemini", "command",
        "mistral", "glm", "kimi", "moonshot", "sonnet", "opus", "haiku",
        "chat", "instruct", "reasoner",
    )

    def score(m: str) -> tuple[int, str]:
        ml = m.lower()
        if any(x in ml for x in deprioritize):
            return (2, ml)
        if any(x in ml for x in prioritize):
            return (0, ml)
        return (1, ml)

    # 去重保序
    seen: set[str] = set()
    uniq: list[str] = []
    for mid in ids:
        if not mid or mid in seen:
            continue
        seen.add(mid)
        uniq.append(mid)
    return sorted(uniq, key=score)


async def _resolve_api_key(
    repo: SettingRepository,
    provided: Optional[str],
) -> str:
    from backend.core.config import settings

    if provided and "..." not in str(provided) and provided != "***":
        return str(provided)
    row = await repo.get_by_key("llm_api_key")
    if row and isinstance(row.value, str) and row.value:
        return row.value
    return settings.llm_api_key or ""


async def fetch_provider_models(
    provider: str,
    base_url: str,
    api_key: str,
) -> dict[str, Any]:
    """
    从供应商实时拉取模型列表（非 mock）。
    支持 Ollama / OpenAI / Anthropic / OpenAI 兼容端点。
    """
    import aiohttp

    base_url = (base_url or "").rstrip("/")
    provider = (provider or "").strip().lower()
    timeout = aiohttp.ClientTimeout(total=20)

    try:
        if provider == "ollama":
            url = f"{base_url}/api/tags"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=timeout) as resp:
                    text = await resp.text()
                    if resp.status >= 400:
                        return {
                            "ok": False,
                            "models": [],
                            "message": f"无法拉取 Ollama 模型 (HTTP {resp.status})",
                            "detail": text[:300],
                        }
                    body = await resp.json(content_type=None)
                    names = [
                        m.get("name") or m.get("model") or ""
                        for m in (body.get("models") or [])
                    ]
                    models = _sort_model_ids([n for n in names if n])
                    return {
                        "ok": True,
                        "models": models,
                        "message": f"已从 Ollama 拉取 {len(models)} 个模型",
                        "source": url,
                    }

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if provider == "anthropic":
            url = f"{base_url}/v1/models"
            headers["x-api-key"] = api_key or ""
            headers["anthropic-version"] = "2023-06-01"
        else:
            # openai / openai-compatible / vllm
            url = _models_url(base_url)
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

        if provider not in ("ollama", "vllm") and not api_key:
            if "localhost" not in base_url and "127.0.0.1" not in base_url:
                return {
                    "ok": False,
                    "models": [],
                    "message": "请先填写 API Key 再拉取模型列表",
                }

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=timeout) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    hint = "请检查 API Key 与服务地址"
                    if resp.status in (401, 403):
                        hint = "认证失败：API Key 无效或权限不足"
                    elif resp.status == 404:
                        hint = "该服务不支持 /models 接口，请手动填写模型名"
                    return {
                        "ok": False,
                        "models": [],
                        "message": f"拉取模型失败 (HTTP {resp.status})。{hint}",
                        "detail": text[:300],
                        "source": url,
                    }
                try:
                    body = await resp.json(content_type=None)
                except Exception:
                    return {
                        "ok": False,
                        "models": [],
                        "message": "供应商返回了无法解析的响应",
                        "detail": text[:300],
                        "source": url,
                    }

                raw_ids: list[str] = []
                if isinstance(body, dict):
                    data = body.get("data")
                    if isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict):
                                mid = item.get("id") or item.get("name") or item.get("model")
                                if mid:
                                    raw_ids.append(str(mid))
                            elif isinstance(item, str):
                                raw_ids.append(item)
                    # 少数兼容端：{ models: ["a","b"] } 或 { models: [{id}] }
                    models_field = body.get("models")
                    if isinstance(models_field, list) and not raw_ids:
                        for item in models_field:
                            if isinstance(item, dict):
                                mid = item.get("id") or item.get("name") or item.get("model")
                                if mid:
                                    raw_ids.append(str(mid))
                            elif isinstance(item, str):
                                raw_ids.append(item)
                elif isinstance(body, list):
                    for item in body:
                        if isinstance(item, dict):
                            mid = item.get("id") or item.get("name")
                            if mid:
                                raw_ids.append(str(mid))
                        elif isinstance(item, str):
                            raw_ids.append(item)

                models = _sort_model_ids(raw_ids)
                if not models:
                    return {
                        "ok": False,
                        "models": [],
                        "message": "连接成功，但该服务未返回模型列表（部分供应商如讯飞 MaaS 不支持此接口）。请手动填写模型名后直接「保存并测试」。",
                        "source": url,
                    }
                return {
                    "ok": True,
                    "models": models,
                    "message": f"已从供应商拉取 {len(models)} 个模型",
                    "source": url,
                }
    except aiohttp.ClientConnectorError:
        return {
            "ok": False,
            "models": [],
            "message": f"无法连接到 {base_url}。请确认服务已启动、地址正确。",
        }
    except TimeoutError:
        return {
            "ok": False,
            "models": [],
            "message": "拉取模型超时。服务响应过慢或不可达。",
        }
    except Exception as e:
        return {
            "ok": False,
            "models": [],
            "message": f"拉取模型失败：{e}",
        }


@router.get("/presets")
async def list_provider_presets(
    current_user: Annotated[UserRead, Depends(require_admin)],
):
    """返回面向新手的服务商预设列表（不含 mock 模型列表）"""
    # 去掉内置 models 假数据，避免前端误用；默认模型名仅保留在 llm.llm_model
    cleaned = []
    for p in PROVIDER_PRESETS:
        item = dict(p)
        item["models"] = []
        cleaned.append(item)
    return cleaned


# ── 知识检索栈预设（Embedding / Reranker / Qdrant）──

# layer: stack=一键全套 | embedding=仅向量 | qdrant=仅向量库 | reranker=仅精排 | toggle=开关
RAG_STACK_PRESETS: list[dict[str, Any]] = [
    # ── 一键全套 ──
    {
        "id": "llamacpp-local-full",
        "name": "本机 llama.cpp 全套",
        "badge": "本机推荐",
        "layer": "stack",
        "description": "Embedding :8086 + Reranker :8087 + Qdrant :6333。适合已部署本地推理服务的机器（模型名按实际部署填写）。",
        "icon": "🖥️",
        "help_text": "确认 llama-server 在 8086/8087 监听，Qdrant 在 6333 运行后一键保存。",
        "items": {
            "rag_enabled": True,
            "embedding_provider": "openai-compatible",
            "embedding_base_url": "http://127.0.0.1:8086",
            "embedding_model": "default",
            "embedding_api_key": "",
            "qdrant_url": "http://localhost:6333",
            "qdrant_collection": "knowledge_base",
            "reranker_provider": "openai-compatible",
            "reranker_base_url": "http://127.0.0.1:8087",
            "reranker_model": "default",
            "reranker_api_key": "",
        },
    },
    {
        "id": "siliconflow-full",
        "name": "SiliconFlow 云端全套",
        "badge": "国内云",
        "layer": "stack",
        "description": "BAAI/bge-m3 向量 + BGE-Reranker + 本机 Qdrant。国内直连，免费额度多。",
        "icon": "🟢",
        "help_text": "1) siliconflow.cn 创建 Key  2) docker 启动 Qdrant :6333  3) 填 Key 后保存",
        "items": {
            "rag_enabled": True,
            "embedding_provider": "openai-compatible",
            "embedding_base_url": "https://api.siliconflow.cn/v1",
            "embedding_model": "BAAI/bge-m3",
            "embedding_api_key": "",
            "qdrant_url": "http://localhost:6333",
            "qdrant_collection": "knowledge_base",
            "reranker_provider": "openai-compatible",
            "reranker_base_url": "https://api.siliconflow.cn/v1",
            "reranker_model": "BAAI/bge-reranker-v2-m3",
            "reranker_api_key": "",
        },
    },
    {
        "id": "dashscope-full",
        "name": "阿里云 DashScope 全套",
        "badge": "阿里云",
        "layer": "stack",
        "description": "text-embedding-v3 + 本机 Qdrant，与通义共用 Key。",
        "icon": "🟠",
        "help_text": "1) dashscope.console.aliyun.com 创建 Key  2) 启动 Qdrant  3) 填 Key 保存",
        "items": {
            "rag_enabled": True,
            "embedding_provider": "openai-compatible",
            "embedding_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "embedding_model": "text-embedding-v3",
            "embedding_api_key": "",
            "qdrant_url": "http://localhost:6333",
            "qdrant_collection": "knowledge_base",
            "reranker_provider": "",
            "reranker_base_url": "",
            "reranker_model": "",
            "reranker_api_key": "",
        },
    },
    {
        "id": "zhipu-full",
        "name": "智谱 Embedding 全套",
        "badge": "智谱",
        "layer": "stack",
        "description": "embedding-3 + 本机 Qdrant，与 GLM 共用 Key。",
        "icon": "🔷",
        "help_text": "1) open.bigmodel.cn 创建 Key  2) 启动 Qdrant  3) 填 Key 保存",
        "items": {
            "rag_enabled": True,
            "embedding_provider": "openai-compatible",
            "embedding_base_url": "https://open.bigmodel.cn/api/paas/v4",
            "embedding_model": "embedding-3",
            "embedding_api_key": "",
            "qdrant_url": "http://localhost:6333",
            "qdrant_collection": "knowledge_base",
            "reranker_provider": "",
            "reranker_base_url": "",
            "reranker_model": "",
            "reranker_api_key": "",
        },
    },
    {
        "id": "ollama-local",
        "name": "本机 Ollama + Qdrant",
        "badge": "Ollama",
        "layer": "stack",
        "description": "Embedding 用 Ollama nomic-embed-text，向量库本机 Qdrant。",
        "icon": "🦙",
        "help_text": "1) ollama pull nomic-embed-text  2) docker run -p 6333:6333 qdrant/qdrant  3) 保存",
        "items": {
            "rag_enabled": True,
            "embedding_provider": "ollama",
            "embedding_base_url": "http://localhost:11434",
            "embedding_model": "nomic-embed-text",
            "embedding_api_key": "",
            "qdrant_url": "http://localhost:6333",
            "qdrant_collection": "knowledge_base",
            "reranker_provider": "",
            "reranker_base_url": "",
            "reranker_model": "",
            "reranker_api_key": "",
        },
    },
    # ── 仅 Embedding ──
    {
        "id": "embed-llamacpp",
        "name": "本机 llama.cpp Embedding",
        "badge": "仅向量",
        "layer": "embedding",
        "description": "本机 :8086 Embedding 服务（OpenAI 兼容 /v1/embeddings，模型名按实际部署填写）。",
        "icon": "🧬",
        "help_text": "默认 http://127.0.0.1:8086，模型名与本地 llama-server 加载的模型一致",
        "items": {
            "embedding_provider": "openai-compatible",
            "embedding_base_url": "http://127.0.0.1:8086",
            "embedding_model": "default",
            "embedding_api_key": "",
        },
    },
    {
        "id": "embed-siliconflow",
        "name": "SiliconFlow Embedding",
        "badge": "仅向量",
        "layer": "embedding",
        "description": "云端 BAAI/bge-m3。",
        "icon": "🟢",
        "help_text": "需 SiliconFlow API Key",
        "items": {
            "embedding_provider": "openai-compatible",
            "embedding_base_url": "https://api.siliconflow.cn/v1",
            "embedding_model": "BAAI/bge-m3",
            "embedding_api_key": "",
        },
    },
    {
        "id": "embed-dashscope",
        "name": "DashScope Embedding",
        "badge": "仅向量",
        "layer": "embedding",
        "description": "阿里云 text-embedding-v3。",
        "icon": "🟠",
        "help_text": "需 DashScope API Key",
        "items": {
            "embedding_provider": "openai-compatible",
            "embedding_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "embedding_model": "text-embedding-v3",
            "embedding_api_key": "",
        },
    },
    {
        "id": "embed-zhipu",
        "name": "智谱 Embedding",
        "badge": "仅向量",
        "layer": "embedding",
        "description": "智谱 embedding-3。",
        "icon": "🔷",
        "help_text": "需智谱 API Key",
        "items": {
            "embedding_provider": "openai-compatible",
            "embedding_base_url": "https://open.bigmodel.cn/api/paas/v4",
            "embedding_model": "embedding-3",
            "embedding_api_key": "",
        },
    },
    {
        "id": "embed-ollama",
        "name": "Ollama Embedding",
        "badge": "仅向量",
        "layer": "embedding",
        "description": "本机 Ollama nomic-embed-text。",
        "icon": "🦙",
        "help_text": "ollama pull nomic-embed-text",
        "items": {
            "embedding_provider": "ollama",
            "embedding_base_url": "http://localhost:11434",
            "embedding_model": "nomic-embed-text",
            "embedding_api_key": "",
        },
    },
    {
        "id": "embed-compatible",
        "name": "兼容接口 Embedding",
        "badge": "自定义",
        "layer": "embedding",
        "description": "任意 OpenAI 兼容 /v1/embeddings（TEI、vLLM、OneAPI…）。",
        "icon": "⚙️",
        "help_text": "填写 base_url、模型名与可选 API Key",
        "items": {
            "embedding_provider": "openai-compatible",
            "embedding_base_url": "http://127.0.0.1:8086",
            "embedding_model": "bge-m3",
            "embedding_api_key": "",
        },
    },
    # ── 仅 Qdrant ──
    {
        "id": "qdrant-local",
        "name": "本机 Qdrant",
        "badge": "向量库",
        "layer": "qdrant",
        "description": "localhost:6333，collection=knowledge_base。",
        "icon": "📦",
        "help_text": "docker run -p 6333:6333 -p 6334:6334 qdrant/qdrant",
        "items": {
            "qdrant_url": "http://localhost:6333",
            "qdrant_collection": "knowledge_base",
        },
    },
    {
        "id": "qdrant-remote",
        "name": "远程 Qdrant",
        "badge": "向量库",
        "layer": "qdrant",
        "description": "填写局域网/云端 Qdrant 地址。",
        "icon": "☁️",
        "help_text": "例如 http://192.168.x.x:6333",
        "items": {
            "qdrant_url": "http://127.0.0.1:6333",
            "qdrant_collection": "knowledge_base",
        },
    },
    # ── 仅 Reranker ──
    {
        "id": "rerank-llamacpp",
        "name": "本机 llama.cpp Reranker",
        "badge": "精排",
        "layer": "reranker",
        "description": "本机 :8087 Reranker 服务（模型名按实际部署填写）。",
        "icon": "🎯",
        "help_text": "默认 http://127.0.0.1:8087",
        "items": {
            "reranker_provider": "openai-compatible",
            "reranker_base_url": "http://127.0.0.1:8087",
            "reranker_model": "default",
            "reranker_api_key": "",
        },
    },
    {
        "id": "rerank-siliconflow",
        "name": "SiliconFlow Reranker",
        "badge": "精排",
        "layer": "reranker",
        "description": "云端 BAAI/bge-reranker-v2-m3。",
        "icon": "🟢",
        "help_text": "与 SiliconFlow Embedding 可用同一 Key",
        "items": {
            "reranker_provider": "openai-compatible",
            "reranker_base_url": "https://api.siliconflow.cn/v1",
            "reranker_model": "BAAI/bge-reranker-v2-m3",
            "reranker_api_key": "",
        },
    },
    {
        "id": "rerank-cohere",
        "name": "Cohere Reranker",
        "badge": "精排",
        "layer": "reranker",
        "description": "Cohere multilingual rerank v3。",
        "icon": "🌍",
        "help_text": "dashboard.cohere.com 申请 Key（海外）",
        "items": {
            "reranker_provider": "cohere",
            "reranker_base_url": "https://api.cohere.ai",
            "reranker_model": "rerank-multilingual-v3.0",
            "reranker_api_key": "",
        },
    },
    {
        "id": "rerank-compatible",
        "name": "兼容接口 Reranker",
        "badge": "自定义",
        "layer": "reranker",
        "description": "任意 OpenAI 兼容 / 自定义 rerank 服务。",
        "icon": "⚙️",
        "help_text": "填写 base_url 与模型名",
        "items": {
            "reranker_provider": "openai-compatible",
            "reranker_base_url": "http://127.0.0.1:8087",
            "reranker_model": "reranker",
            "reranker_api_key": "",
        },
    },
    {
        "id": "rerank-off",
        "name": "关闭 Reranker",
        "badge": "可选",
        "layer": "reranker",
        "description": "仅用向量召回，不做二次精排。",
        "icon": "➖",
        "help_text": "新手可先关精排，把 Embedding+Qdrant 跑通再开",
        "items": {
            "reranker_provider": "",
            "reranker_base_url": "",
            "reranker_model": "",
            "reranker_api_key": "",
        },
    },
    # ── 开关 ──
    {
        "id": "rag-on",
        "name": "开启自动 RAG",
        "badge": "开关",
        "layer": "toggle",
        "description": "会话中自动注入知识库检索结果。",
        "icon": "✅",
        "help_text": "需先配好 Embedding 与 Qdrant",
        "items": {"rag_enabled": True},
    },
    {
        "id": "rag-off",
        "name": "关闭自动 RAG",
        "badge": "开关",
        "layer": "toggle",
        "description": "禁用自动注入（工具仍可手动调用检索）。",
        "icon": "🚫",
        "help_text": "未配向量服务时请关闭，避免报错",
        "items": {"rag_enabled": False},
    },
]


@router.get("/rag-presets")
async def list_rag_presets(
    current_user: Annotated[UserRead, Depends(require_admin)],
):
    """知识检索栈（Embedding / Qdrant / Reranker）新手预设。"""
    return RAG_STACK_PRESETS


class TestEmbedBody(BaseModel):
    embedding_provider: Optional[str] = None
    embedding_base_url: Optional[str] = None
    embedding_model: Optional[str] = None
    embedding_api_key: Optional[str] = None


class TestQdrantBody(BaseModel):
    qdrant_url: Optional[str] = None
    qdrant_collection: Optional[str] = None


class TestRerankBody(BaseModel):
    reranker_provider: Optional[str] = None
    reranker_base_url: Optional[str] = None
    reranker_model: Optional[str] = None
    reranker_api_key: Optional[str] = None


@router.post("/test-embedding")
async def test_embedding(
    data: TestEmbedBody,
    current_user: Annotated[UserRead, Depends(require_admin)],
):
    """探测 Embedding：自适应 OpenAI / Ollama / TEI 等多端点。"""
    from backend.core.config import settings as app_settings
    from backend.core.runtime_settings import apply_settings_dict
    from backend.services.embedding.factory import EmbeddingServiceFactory
    from backend.services.embedding.local_compatible import LocalEmbeddingService
    from backend.services.embedding.ollama import OllamaEmbeddingService
    from backend.services.embedding.openai import OpenAIEmbeddingService
    from backend.services.endpoint_probe import normalize_base_url

    items = {k: v for k, v in data.model_dump().items() if v is not None and v != ""}
    if "embedding_base_url" in items and isinstance(items["embedding_base_url"], str):
        items["embedding_base_url"] = normalize_base_url(items["embedding_base_url"]) or items["embedding_base_url"].strip()
    # P1：测试端点不得 apply_settings_dict 污染全局运行时；仅用请求体覆盖本地变量
    provider = str(
        items.get("embedding_provider") or app_settings.embedding_provider or ""
    ).strip().lower()
    base = normalize_base_url(
        str(items.get("embedding_base_url") or app_settings.embedding_base_url or "")
    ) or str(items.get("embedding_base_url") or app_settings.embedding_base_url or "").strip()
    model = str(items.get("embedding_model") or app_settings.embedding_model or "").strip()

    if not base or not model:
        return {
            "ok": False,
            "message": "请填写 Embedding 服务地址和模型名（例：http://127.0.0.1:8086 + 你的模型名）",
        }

    if provider in ("", "none", "null"):
        provider = "openai-compatible"

    try:
        if provider == "ollama":
            svc = OllamaEmbeddingService()
        elif provider == "openai":
            svc = OpenAIEmbeddingService()
        else:
            svc = LocalEmbeddingService()

        EmbeddingServiceFactory.reset()
        vec = await svc.embed_query("takton embedding ping")
        dim = len(vec) if vec else 0
        if dim <= 0:
            return {"ok": False, "message": f"Embedding 返回空向量 · provider={provider} · {base}"}
        used = getattr(svc, "_cached_url", None) or base
        kind = getattr(svc, "_cached_kind", None) or provider
        return {
            "ok": True,
            "message": f"Embedding 正常 · 维度 {dim} · {provider}/{model} · 命中 {used} ({kind})",
            "dimension": dim,
            "model": model,
            "provider": provider,
            "endpoint": used,
        }
    except Exception as e:
        return {
            "ok": False,
            "message": (
                f"Embedding 失败: {e}。provider={provider} base={base} model={model}。"
                " 已尝试 /v1/embeddings、/embeddings、/api/embed、/embed 等主流路径。"
            ),
        }


@router.post("/test-qdrant")
async def test_qdrant(
    data: TestQdrantBody,
    current_user: Annotated[UserRead, Depends(require_admin)],
):
    """探测 Qdrant：尝试 /collections、/qdrant/collections 等路径。"""
    import aiohttp

    from backend.core.config import settings as app_settings
    from backend.core.runtime_settings import apply_settings_dict
    from backend.services.endpoint_probe import probe_qdrant

    items = {k: v for k, v in data.model_dump().items() if v is not None and v != ""}
    # P1：测试探测不写全局 settings
    url = str(items.get("qdrant_url") or app_settings.qdrant_url or "").rstrip("/")
    collection = str(
        items.get("qdrant_collection") or app_settings.qdrant_collection or ""
    )
    if not url:
        return {"ok": False, "message": "请填写 Qdrant URL（例：http://127.0.0.1:6333）"}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as session:
            ok, message, used = await probe_qdrant(session, url)
            if ok:
                return {
                    "ok": True,
                    "message": f"{message} · collection 默认 {collection}",
                    "url": used or url,
                    "collection": collection,
                }
            return {"ok": False, "message": message}
    except Exception as e:
        return {
            "ok": False,
            "message": f"无法连接 Qdrant（{url}）：{e}。可用 Docker：docker run -p 6333:6333 qdrant/qdrant",
        }


@router.post("/test-reranker")
async def test_reranker(
    data: TestRerankBody,
    current_user: Annotated[UserRead, Depends(require_admin)],
):
    """探测 Reranker（失败时仍可依赖向量粗排）。"""
    from backend.core.config import settings as app_settings
    from backend.core.runtime_settings import apply_settings_dict
    from backend.services.reranker.factory import RerankerServiceFactory

    items = {k: v for k, v in data.model_dump().items() if v is not None and v != ""}
    if items:
        apply_settings_dict(items, reset=True)
    try:
        RerankerServiceFactory.reset()
        svc = RerankerServiceFactory.get_service()
        ranked = await svc.rerank(
            "苹果",
            ["香蕉是水果", "苹果富含维生素", "汽车发动机"],
            top_n=2,
        )
        top = ranked[0].text if ranked else ""
        return {
            "ok": True,
            "message": f"Reranker 正常 · {app_settings.reranker_provider}/{app_settings.reranker_model} · top={top[:40]}",
            "provider": app_settings.reranker_provider,
        }
    except Exception as e:
        return {
            "ok": False,
            "message": f"Reranker 失败（可继续仅用向量粗排）: {e}",
        }


@router.post("/list-models")
async def list_remote_models(
    data: TestLLMBody,
    current_user: Annotated[UserRead, Depends(require_admin)],
    repo: Annotated[SettingRepository, Depends(get_setting_repo)],
):
    """
    从供应商服务器实时拉取模型列表。
    需有效 API Key（云服务）或可访问的本地端点（Ollama 等）。
    成功时按 base_url 匹配目录中的供应商并写回 cached_models。
    """
    from backend.core.config import settings

    provider = data.llm_provider or settings.llm_provider
    base_url = (data.llm_base_url or settings.llm_base_url or "").rstrip("/")
    api_key = await _resolve_api_key(repo, data.llm_api_key)
    result = await fetch_provider_models(provider, base_url, api_key)

    # 写回缓存：优先 provider_id，其次 base_url / provider 类型
    models = [str(m).strip() for m in (result.get("models") or []) if str(m).strip()]
    if result.get("ok") and models:
        try:
            catalog = await model_catalog_mod.load_catalog(repo)
            matched = None
            want_pid = str(getattr(data, "provider_id", None) or "").strip()
            if want_pid:
                matched = next(
                    (p for p in (catalog.get("providers") or []) if str(p.get("id") or "") == want_pid),
                    None,
                )
            base_l = base_url.lower().rstrip("/")
            if matched is None:
                for p in catalog.get("providers") or []:
                    pb = str(p.get("llm_base_url") or "").rstrip("/").lower()
                    if base_l and pb == base_l:
                        matched = p
                        break
            if matched is None:
                for p in catalog.get("providers") or []:
                    if str(p.get("llm_provider") or "").lower() == str(provider or "").lower():
                        matched = p
                        break
            if matched is not None:
                catalog = model_catalog_mod.set_provider_cached_models(
                    catalog,
                    str(matched.get("id") or ""),
                    models,
                    active_model=str(data.llm_model or matched.get("active_model") or "") or None,
                )
                await model_catalog_mod.save_catalog(repo, catalog)
                # 带上可直接用于下拉的 models，免前端再 round-trip
                result["catalog"] = model_catalog_mod.mask_catalog_for_client(catalog)
                result["provider_id"] = str(matched.get("id") or "")
        except Exception as e:
            logger.warning("list-models cache write failed: %s", e)

    return result


# ---- 模型目录（Hermes Desktop 风格：多供应商 + 禁用模型 + 对话页切换）----


class SelectModelBody(BaseModel):
    provider_id: str
    model: str
    # 可选：传入当前会话 id 时，同步重建该会话的 LLM 快照，使切换立即生效
    session_id: Optional[str] = None


class SetFallbackModelBody(BaseModel):
    """设置/清空备用模型。provider_id 与 model 同时为空字符串则清空。"""

    provider_id: str = ""
    model: str = ""


class DisableModelBody(BaseModel):
    provider_id: str
    model: str
    disabled: bool = True


class ToggleProviderBody(BaseModel):
    provider_id: str
    enabled: bool = True


class RegisterProviderBody(BaseModel):
    """设置页保存时登记/更新一个供应商到目录。"""

    id: str
    name: str
    icon: str = "🤖"
    preset_id: str | None = None
    llm_provider: str
    llm_base_url: str
    llm_api_key: str | None = None
    llm_model: str | None = None
    set_active: bool = True
    # 首次「拉取模型」后一并写入缓存，避免下拉要等二次 live fetch
    models: list[str] | None = None


@router.post("/oauth/xai/start")
async def xai_oauth_start(
    current_user: Annotated[UserRead, Depends(require_admin)],
):
    """发起 Grok OAuth 设备码登录。"""
    from backend.services.xai_oauth import start_device_login

    return await start_device_login()


class XaiOauthPollBody(BaseModel):
    device_code: str


@router.post("/oauth/xai/poll")
async def xai_oauth_poll(
    data: XaiOauthPollBody,
    request: Request,
    current_user: Annotated[UserRead, Depends(require_admin)],
    repo: Annotated[SettingRepository, Depends(get_setting_repo)],
):
    """
    轮询 OAuth 授权结果；成功则登记 xai-oauth 供应商并设为当前使用。
    """
    from backend.services.xai_oauth import poll_device_login

    result = await poll_device_login(data.device_code)
    if not result.get("ok"):
        return result

    catalog = await model_catalog_mod.load_catalog(repo)
    catalog = model_catalog_mod.save_oauth_credential(
        catalog,
        provider_id="xai-oauth",
        name="Grok OAuth",
        icon="⚡",
        access_token=str(result["access_token"]),
        refresh_token=str(result.get("refresh_token") or ""),
        expires_at=str(result.get("expires_at") or ""),
        base_url=str(result.get("base_url") or "https://api.x.ai/v1"),
        model="grok-4",
        set_active=True,
    )
    await model_catalog_mod.save_catalog(repo, catalog)
    model_catalog_mod.apply_active_to_runtime(catalog)

    # 同步 llm_* 
    await repo.upsert("llm_provider", "openai-compatible", "llm")
    await repo.upsert("llm_base_url", "https://api.x.ai/v1", "llm")
    await repo.upsert("llm_model", catalog.get("active_model") or "grok-4", "llm")
    await repo.upsert("llm_api_key", str(result["access_token"]), "llm")

    await log_action(
        AuditAction.SETTINGS_UPDATE,
        request=request,
        user_id=current_user.id,
        resource_type="model_catalog",
        resource_id="xai-oauth",
        details={"action": "oauth_login"},
    )
    return {
        "ok": True,
        "status": "authorized",
        "message": "Grok OAuth 登录成功，已设为当前供应商",
        "active_provider_id": "xai-oauth",
        "active_model": catalog.get("active_model") or "grok-4",
        "catalog": model_catalog_mod.mask_catalog_for_client(catalog),
    }


@router.post("/oauth/xai/logout")
async def xai_oauth_logout(
    request: Request,
    current_user: Annotated[UserRead, Depends(require_admin)],
    repo: Annotated[SettingRepository, Depends(get_setting_repo)],
):
    """移除 Grok OAuth 供应商及其令牌。"""
    catalog = await model_catalog_mod.load_catalog(repo)
    catalog["providers"] = [
        p for p in catalog.get("providers") or [] if p.get("id") != "xai-oauth"
    ]
    if catalog.get("active_provider_id") == "xai-oauth":
        catalog["active_provider_id"] = ""
        catalog["active_model"] = ""
        if catalog["providers"]:
            p0 = next((p for p in catalog["providers"] if p.get("enabled")), catalog["providers"][0])
            catalog["active_provider_id"] = p0["id"]
            # keep model empty so UI re-selects
    await model_catalog_mod.save_catalog(repo, catalog)
    if catalog.get("active_provider_id"):
        model_catalog_mod.apply_active_to_runtime(catalog)
    await log_action(
        AuditAction.SETTINGS_UPDATE,
        request=request,
        user_id=current_user.id,
        resource_type="model_catalog",
        resource_id="xai-oauth",
        details={"action": "oauth_logout"},
    )
    return {
        "ok": True,
        "message": "已退出 Grok OAuth",
        "catalog": model_catalog_mod.mask_catalog_for_client(catalog),
    }


@router.post("/oauth/openai/start")
async def openai_oauth_start(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """发起 ChatGPT 会员 OAuth（PKCE · Codex 客户端），并尽量监听 localhost:1455。

    任意已登录用户可发起（不强制 superuser）。失败返回 ok:false，避免裸 500。
    """
    try:
        from backend.services.openai_oauth import start_pkce_login_async
    except ImportError as e:
        logger.exception("openai oauth import failed")
        return {
            "ok": False,
            "status": "error",
            "message": (
                "ChatGPT OAuth 依赖未安装（需要 aiohttp）。"
                "请在 PC 端执行: pip install aiohttp 后重启。"
            ),
            "detail": str(e),
        }
    try:
        return await start_pkce_login_async()
    except Exception as e:
        logger.exception("openai oauth start failed for user=%s", getattr(current_user, "email", None))
        return {
            "ok": False,
            "status": "error",
            "message": f"无法发起 ChatGPT 登录：{e}",
            "detail": str(e),
        }


class OpenAIOauthCompleteBody(BaseModel):
    callback_url: str
    state: str | None = None


class OpenAIOauthPollBody(BaseModel):
    state: str | None = None


async def _activate_openai_chatgpt_oauth(
    *,
    result: dict,
    request: Request,
    current_user: UserRead,
    repo: SettingRepository,
) -> dict:
    """把 OAuth 令牌写入 model catalog 并设为当前供应商。"""
    from backend.services.openai_oauth import OPENAI_CODEX_LOCAL_BASE, mark_result_consumed

    base_url = str(result.get("base_url") or OPENAI_CODEX_LOCAL_BASE)
    try:
        host = request.headers.get("host") or "127.0.0.1:8090"
        if "8090" in base_url and host and "127.0.0.1" in base_url:
            base_url = f"http://{host.split(',')[0].strip()}/api/llm-proxy/openai-codex/v1"
    except Exception:
        pass

    from backend.api.routes.openai_codex_proxy import CODEX_OAUTH_MODELS

    catalog = await model_catalog_mod.load_catalog(repo)
    catalog = model_catalog_mod.save_oauth_credential(
        catalog,
        provider_id="openai-chatgpt-oauth",
        name="ChatGPT 会员 (OAuth)",
        icon="💠",
        access_token=str(result["access_token"]),
        refresh_token=str(result.get("refresh_token") or ""),
        expires_at=str(result.get("expires_at") or ""),
        base_url=base_url,
        model="gpt-5.6",
        set_active=True,
        account_id=str(result.get("account_id") or ""),
        auth_mode="oauth_pkce",
        label="ChatGPT OAuth",
    )
    # 始终用最新 Codex 型号表刷新缓存（避免只看到登录时的旧列表）
    catalog = model_catalog_mod.set_provider_cached_models(
        catalog,
        "openai-chatgpt-oauth",
        list(CODEX_OAUTH_MODELS),
        active_model="gpt-5.6",
    )
    await model_catalog_mod.save_catalog(repo, catalog)
    model_catalog_mod.apply_active_to_runtime(catalog)

    await repo.upsert("llm_provider", "openai-compatible", "llm")
    await repo.upsert("llm_base_url", base_url, "llm")
    await repo.upsert("llm_model", catalog.get("active_model") or "gpt-5.6", "llm")
    await repo.upsert("llm_api_key", str(result["access_token"]), "llm")
    if result.get("account_id"):
        await repo.upsert("openai_chatgpt_account_id", str(result["account_id"]), "llm")

    mark_result_consumed(str(result.get("state") or "") or None)

    await log_action(
        AuditAction.SETTINGS_UPDATE,
        request=request,
        user_id=current_user.id,
        resource_type="model_catalog",
        resource_id="openai-chatgpt-oauth",
        details={"action": "oauth_login"},
    )
    return {
        "ok": True,
        "status": "authorized",
        "message": "ChatGPT OAuth 登录成功，已设为当前供应商（订阅额度）",
        "active_provider_id": "openai-chatgpt-oauth",
        "active_model": catalog.get("active_model") or "gpt-5.6",
        "catalog": model_catalog_mod.mask_catalog_for_client(catalog),
    }


@router.post("/oauth/openai/poll")
async def openai_oauth_poll(
    data: OpenAIOauthPollBody,
    request: Request,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[SettingRepository, Depends(get_setting_repo)],
):
    """轮询 1455 回调是否已换好 token；成功则激活供应商。"""
    try:
        from backend.services.openai_oauth import poll_login_result
    except ImportError as e:
        return {
            "ok": False,
            "status": "error",
            "message": "ChatGPT OAuth 依赖未安装（aiohttp）",
            "detail": str(e),
        }
    try:
        result = poll_login_result(data.state)
        if result.get("status") == "pending":
            return result
        if not result.get("ok") or result.get("status") != "authorized":
            return result
        if not result.get("access_token"):
            return {"ok": False, "status": "error", "message": "缺少 access_token"}
        return await _activate_openai_chatgpt_oauth(
            result=result,
            request=request,
            current_user=current_user,
            repo=repo,
        )
    except Exception as e:
        logger.exception("openai oauth poll failed")
        return {"ok": False, "status": "error", "message": f"轮询失败：{e}", "detail": str(e)}


@router.post("/oauth/openai/complete")
async def openai_oauth_complete(
    data: OpenAIOauthCompleteBody,
    request: Request,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[SettingRepository, Depends(get_setting_repo)],
):
    """用浏览器回调 URL 完成登录，登记 openai-chatgpt-oauth 供应商。"""
    try:
        from backend.services.openai_oauth import complete_pkce_login
    except ImportError as e:
        return {
            "ok": False,
            "status": "error",
            "message": "ChatGPT OAuth 依赖未安装（aiohttp）",
            "detail": str(e),
        }
    try:
        result = await complete_pkce_login(data.callback_url, state=data.state)
        if not result.get("ok"):
            return result
        return await _activate_openai_chatgpt_oauth(
            result=result,
            request=request,
            current_user=current_user,
            repo=repo,
        )
    except Exception as e:
        logger.exception("openai oauth complete failed")
        return {"ok": False, "status": "error", "message": f"完成登录失败：{e}", "detail": str(e)}



@router.post("/oauth/openai/logout")
async def openai_oauth_logout(
    request: Request,
    current_user: Annotated[UserRead, Depends(require_admin)],
    repo: Annotated[SettingRepository, Depends(get_setting_repo)],
):
    """移除 ChatGPT OAuth 供应商。"""
    catalog = await model_catalog_mod.load_catalog(repo)
    catalog["providers"] = [
        p
        for p in catalog.get("providers") or []
        if p.get("id") != "openai-chatgpt-oauth"
    ]
    if catalog.get("active_provider_id") == "openai-chatgpt-oauth":
        catalog["active_provider_id"] = ""
        catalog["active_model"] = ""
        if catalog["providers"]:
            p0 = next(
                (p for p in catalog["providers"] if p.get("enabled")),
                catalog["providers"][0],
            )
            catalog["active_provider_id"] = p0["id"]
    await model_catalog_mod.save_catalog(repo, catalog)
    if catalog.get("active_provider_id"):
        model_catalog_mod.apply_active_to_runtime(catalog)
    await log_action(
        AuditAction.SETTINGS_UPDATE,
        request=request,
        user_id=current_user.id,
        resource_type="model_catalog",
        resource_id="openai-chatgpt-oauth",
        details={"action": "oauth_logout"},
    )
    return {
        "ok": True,
        "message": "已退出 ChatGPT OAuth",
        "catalog": model_catalog_mod.mask_catalog_for_client(catalog),
    }


@router.get("/model-catalog")
async def get_model_catalog(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[SettingRepository, Depends(get_setting_repo)],
    fetch_models: bool = True,
):
    """
    返回已配置供应商目录。
    fetch_models=true 时为每个启用的供应商实时拉取模型列表，并标注 disabled；
    成功拉取会写回 cached_models。fetch_models=false 时用缓存回显，避免回访变 0。
    """
    catalog = await model_catalog_mod.load_catalog(repo)
    cleaned = model_catalog_mod.prune_orphan_providers(catalog)
    if cleaned != catalog:
        catalog = cleaned
        try:
            await model_catalog_mod.save_catalog(repo, catalog)
        except Exception:
            pass
    # OAuth token 临近过期时刷新
    try:
        catalog = await model_catalog_mod.ensure_oauth_token_fresh(catalog)
        await model_catalog_mod.save_catalog(repo, catalog)
    except Exception:
        pass

    active_pid = str(catalog.get("active_provider_id") or "")
    active_model = str(catalog.get("active_model") or "")
    catalog_dirty = False

    public = model_catalog_mod.mask_catalog_for_client(catalog)

    providers_out: list[dict[str, Any]] = []
    for p in public.get("providers") or []:
        entry = dict(p)
        entry["models"] = []
        entry["fetch_ok"] = None
        entry["fetch_message"] = ""
        disabled_set = set(p.get("disabled_models") or [])
        raw = next(
            (x for x in catalog["providers"] if x["id"] == p["id"]),
            None,
        )

        if fetch_models and p.get("enabled", True):
            api_key = (raw or {}).get("llm_api_key") or ""
            listed = await fetch_provider_models(
                p.get("llm_provider") or "openai-compatible",
                p.get("llm_base_url") or "",
                api_key,
            )
            live_ids = [str(mid).strip() for mid in (listed.get("models") or []) if str(mid).strip()]
            entry["fetch_ok"] = bool(listed.get("ok"))
            entry["fetch_message"] = listed.get("message") or ""

            if listed.get("ok") and live_ids and raw is not None:
                catalog = model_catalog_mod.set_provider_cached_models(
                    catalog,
                    str(p.get("id") or ""),
                    live_ids,
                    active_model=(raw.get("active_model") or None),
                )
                catalog_dirty = True
                raw = next(
                    (x for x in catalog["providers"] if x["id"] == p["id"]),
                    raw,
                )

            # 拉取失败时回退缓存，避免界面闪 0
            model_ids = live_ids if (listed.get("ok") and live_ids) else (
                model_catalog_mod.provider_models_for_display(
                    raw or p,
                    global_active_provider_id=active_pid,
                    global_active_model=active_model,
                )
            )
            entry["models"] = [
                {"id": mid, "disabled": mid in disabled_set} for mid in model_ids
            ]
        else:
            model_ids = model_catalog_mod.provider_models_for_display(
                raw or p,
                global_active_provider_id=active_pid,
                global_active_model=active_model,
            )
            # 确保 disabled 里有但缓存没有的也显示（方便管理）
            for mid in p.get("disabled_models") or []:
                m = str(mid).strip()
                if m and m not in model_ids:
                    model_ids.append(m)
            entry["models"] = [
                {"id": mid, "disabled": mid in disabled_set} for mid in model_ids
            ]
            entry["fetch_ok"] = None
            entry["fetch_message"] = "cached" if model_ids else ""
        providers_out.append(entry)

    if catalog_dirty:
        try:
            await model_catalog_mod.save_catalog(repo, catalog)
        except Exception as e:
            logger.warning("persist model cache failed: %s", e)

    return {
            "active_provider_id": public.get("active_provider_id") or "",
            "active_model": public.get("active_model") or "",
            "fallback_provider_id": public.get("fallback_provider_id") or "",
            "fallback_model": public.get("fallback_model") or "",
            "providers": providers_out,
        }


@router.post("/model-catalog/register")
async def register_provider_in_catalog(
    data: RegisterProviderBody,
    request: Request,
    current_user: Annotated[UserRead, Depends(require_admin)],
    repo: Annotated[SettingRepository, Depends(get_setting_repo)],
):
    """设置页保存供应商后登记到多供应商目录。"""
    if not (data.llm_base_url or "").strip() and data.llm_provider != "ollama":
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail="llm_base_url 不能为空")
    catalog = await model_catalog_mod.load_catalog(repo)
    catalog = model_catalog_mod.prune_orphan_providers(catalog)
    catalog = model_catalog_mod.upsert_provider(
        catalog,
        provider_id=data.id,
        name=data.name,
        llm_provider=data.llm_provider,
        llm_base_url=data.llm_base_url,
        llm_api_key=data.llm_api_key,
        icon=data.icon,
        preset_id=data.preset_id,
        set_active=data.set_active,
        active_model=data.llm_model,
    )
    # 把设置页刚拉取到的模型列表写进缓存，立刻可供下拉/子代理使用
    if data.models:
        cleaned = [str(m).strip() for m in data.models if str(m).strip()]
        if cleaned:
            catalog = model_catalog_mod.set_provider_cached_models(
                catalog,
                data.id,
                cleaned,
                active_model=data.llm_model,
            )
    await model_catalog_mod.save_catalog(repo, catalog)
    if data.set_active:
        model_catalog_mod.apply_active_to_runtime(catalog)
        # 同步写回 llm_* 便于兼容旧逻辑
        items = {
            "llm_provider": data.llm_provider,
            "llm_base_url": data.llm_base_url,
            "llm_model": data.llm_model or catalog.get("active_model") or "",
        }
        if data.llm_api_key and "..." not in data.llm_api_key:
            items["llm_api_key"] = data.llm_api_key
        for k, v in items.items():
            if v == "" and k == "llm_api_key":
                continue
            await repo.upsert(k, v, "llm")

    await log_action(
        AuditAction.SETTINGS_UPDATE,
        request=request,
        user_id=current_user.id,
        resource_type="model_catalog",
        resource_id=data.id,
        details={"action": "register", "model": data.llm_model},
    )
    return {
        "ok": True,
        "catalog": model_catalog_mod.mask_catalog_for_client(catalog),
        "message": f"已登记供应商「{data.name}」",
    }


@router.post("/model-catalog/select")
async def select_active_model(
    data: SelectModelBody,
    request: Request,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[SettingRepository, Depends(get_setting_repo)],
    session_repo=Depends(get_session_repo),
):
    """对话页切换当前供应商 + 模型，立即生效。"""
    catalog = await model_catalog_mod.load_catalog(repo)
    provider = next((p for p in catalog["providers"] if p["id"] == data.provider_id), None)
    if provider is None:
        raise HTTPException(status_code=404, detail="供应商不存在，请先在设置中配置")
    if not provider.get("enabled", True):
        raise HTTPException(status_code=400, detail="该供应商已禁用")
    if data.model in (provider.get("disabled_models") or []):
        raise HTTPException(status_code=400, detail="该模型已禁用，请先重新启用")

    catalog["active_provider_id"] = data.provider_id
    catalog["active_model"] = data.model
    try:
        catalog = await model_catalog_mod.ensure_oauth_token_fresh(
            catalog, provider_id=data.provider_id
        )
    except Exception:
        pass
    await model_catalog_mod.save_catalog(repo, catalog)
    model_catalog_mod.apply_active_to_runtime(catalog)

    from backend.core import model_gen_params as gen_params_mod

    # Prefer user-saved per-model gen params; seed from limits on first use
    slot = await gen_params_mod.get_params(
        repo, data.provider_id, data.model, create_if_missing=True
    )
    await gen_params_mod.apply_params_to_global_settings(repo, slot)

    # 同步 llm_* 连接设置
    await repo.upsert("llm_provider", provider["llm_provider"], "llm")
    await repo.upsert("llm_base_url", provider["llm_base_url"], "llm")
    await repo.upsert("llm_model", data.model, "llm")
    if provider.get("llm_api_key"):
        await repo.upsert("llm_api_key", provider["llm_api_key"], "llm")

    await log_action(
        AuditAction.SETTINGS_UPDATE,
        request=request,
        user_id=current_user.id,
        resource_type="model_catalog",
        resource_id=f"{data.provider_id}/{data.model}",
        details={
            "action": "select",
            "context_window": slot["context_window"],
            "max_tokens": slot["max_tokens"],
            "temperature": slot["temperature"],
            "reasoning_effort": slot.get("reasoning_effort"),
        },
    )
    # 可选：同步更新当前会话的 LLM 快照，使对话框内切换立即生效
    # （session 创建时快照被锁定，不改则本会话仍走旧 provider，导致"切不过去"）
    if data.session_id:
        try:
            import uuid as _uuid

            sid = _uuid.UUID(str(data.session_id))
            new_snap = model_catalog_mod.build_llm_snapshot(
                provider,
                data.model,
                temperature=slot.get("temperature"),
                max_tokens=slot.get("max_tokens"),
                context_window=slot.get("context_window"),
                reasoning_effort=slot.get("reasoning_effort"),
            )
            # 键级合并：只写 llm 快照键，不覆盖 _goal/_agent_checkpoint 等并发键
            await session_repo.merge_config_keys(sid, {"llm": new_snap})
            logger.info(
                "session %s llm snapshot updated on provider switch -> %s/%s",
                sid,
                data.provider_id,
                data.model,
            )
        except (ValueError, AttributeError) as e:
            logger.warning("invalid session_id on model select: %s", e)
        except Exception as e:  # noqa: BLE001
            # 快照更新失败不影响切换本身
            logger.warning("update session llm snapshot failed: %s", e)
    _notify_settings_changed(
        current_user.id,
        [
            "active_provider_id",
            "active_model",
            "llm_provider",
            "llm_model",
            "llm_base_url",
            "temperature",
            "max_tokens",
            "context_window",
            gen_params_mod.SETTING_KEY,
        ],
    )
    return {
        "ok": True,
        "active_provider_id": data.provider_id,
        "active_model": data.model,
        "provider_name": provider.get("name") or data.provider_id,
        "context_window": slot["context_window"],
        "max_tokens": slot["max_tokens"],
        "temperature": slot["temperature"],
        "gen_params": slot,
        "message": (
            f"已切换到 {provider.get('name')} / {data.model}"
            f"（温度 {slot['temperature']} · 上下文 {slot['context_window']//1000}K · 生成上限 {slot['max_tokens']}）"
        ),
    }


@router.post("/model-catalog/fallback")
async def set_fallback_model(
    data: SetFallbackModelBody,
    request: Request,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[SettingRepository, Depends(get_setting_repo)],
):
    """设置主模型失败时的备用模型（仅存目录，供运行时/子代理池读取）。"""
    catalog = await model_catalog_mod.load_catalog(repo)
    pid = (data.provider_id or "").strip()
    model = (data.model or "").strip()

    if not pid and not model:
        catalog["fallback_provider_id"] = ""
        catalog["fallback_model"] = ""
        await model_catalog_mod.save_catalog(repo, catalog)
        _notify_settings_changed(current_user.id, ["fallback_provider_id", "fallback_model"])
        return {
            "ok": True,
            "fallback_provider_id": "",
            "fallback_model": "",
            "message": "已清除备用模型",
        }

    provider = next((p for p in catalog["providers"] if p["id"] == pid), None)
    if provider is None:
        raise HTTPException(status_code=404, detail="供应商不存在，请先在设置中配置")
    if not provider.get("enabled", True):
        raise HTTPException(status_code=400, detail="该供应商已禁用")
    if not model:
        raise HTTPException(status_code=400, detail="请选择模型")
    if model in (provider.get("disabled_models") or []):
        raise HTTPException(status_code=400, detail="该模型已禁用")

    catalog["fallback_provider_id"] = pid
    catalog["fallback_model"] = model
    await model_catalog_mod.save_catalog(repo, catalog)
    await log_action(
        AuditAction.SETTINGS_UPDATE,
        request=request,
        user_id=current_user.id,
        resource_type="model_catalog",
        resource_id=f"fallback:{pid}/{model}",
        details={"action": "set_fallback"},
    )
    _notify_settings_changed(current_user.id, ["fallback_provider_id", "fallback_model"])
    return {
        "ok": True,
        "fallback_provider_id": pid,
        "fallback_model": model,
        "provider_name": provider.get("name") or pid,
        "message": f"备用模型已设为 {provider.get('name') or pid} / {model}",
    }


@router.post("/model-catalog/disable-model")
async def disable_catalog_model(
    data: DisableModelBody,
    request: Request,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[SettingRepository, Depends(get_setting_repo)],
):
    """禁用/启用某个供应商下的模型（仍保留在目录中，选择器中可重新启用）。"""
    catalog = await model_catalog_mod.load_catalog(repo)
    catalog = model_catalog_mod.set_model_disabled(
        catalog, data.provider_id, data.model, data.disabled
    )
    await model_catalog_mod.save_catalog(repo, catalog)
    await log_action(
        AuditAction.SETTINGS_UPDATE,
        request=request,
        user_id=current_user.id,
        resource_type="model_catalog",
        resource_id=f"{data.provider_id}/{data.model}",
        details={"action": "disable" if data.disabled else "enable"},
    )
    return {
        "ok": True,
        "disabled": data.disabled,
        "message": f"{'已禁用' if data.disabled else '已启用'}模型 {data.model}",
    }


@router.post("/model-catalog/toggle-provider")
async def toggle_catalog_provider(
    data: ToggleProviderBody,
    request: Request,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[SettingRepository, Depends(get_setting_repo)],
):
    catalog = await model_catalog_mod.load_catalog(repo)
    catalog = model_catalog_mod.set_provider_enabled(catalog, data.provider_id, data.enabled)
    await model_catalog_mod.save_catalog(repo, catalog)
    await log_action(
        AuditAction.SETTINGS_UPDATE,
        request=request,
        user_id=current_user.id,
        resource_type="model_catalog",
        resource_id=data.provider_id,
        details={"action": "enable_provider" if data.enabled else "disable_provider"},
    )
    return {
        "ok": True,
        "enabled": data.enabled,
        "message": f"供应商已{'启用' if data.enabled else '禁用'}",
    }


class CredentialBody(BaseModel):
    provider_id: str
    credential_id: Optional[str] = None
    label: str = "Key"
    api_key: str
    set_active: bool = True


class SelectCredentialBody(BaseModel):
    provider_id: str
    credential_id: str


class DeleteCredentialBody(BaseModel):
    provider_id: str
    credential_id: str


@router.post("/model-catalog/credentials")
async def upsert_provider_credential(
    data: CredentialBody,
    request: Request,
    current_user: Annotated[UserRead, Depends(require_admin)],
    repo: Annotated[SettingRepository, Depends(get_setting_repo)],
):
    """为同一供应商新增或更新一条 API Key。"""
    catalog = await model_catalog_mod.load_catalog(repo)
    try:
        catalog = model_catalog_mod.add_or_update_credential(
            catalog,
            data.provider_id,
            credential_id=data.credential_id,
            label=data.label or "Key",
            api_key=data.api_key,
            set_active=data.set_active,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    await model_catalog_mod.save_catalog(repo, catalog)
    if data.set_active and catalog.get("active_provider_id") == data.provider_id:
        model_catalog_mod.apply_active_to_runtime(catalog)
        # 同步 llm_api_key 设置
        p = next((x for x in catalog["providers"] if x["id"] == data.provider_id), None)
        if p and p.get("llm_api_key"):
            await repo.upsert("llm_api_key", p["llm_api_key"], "llm")
    await log_action(
        AuditAction.SETTINGS_UPDATE,
        request=request,
        user_id=current_user.id,
        resource_type="model_catalog",
        resource_id=data.provider_id,
        details={"action": "upsert_credential", "label": data.label},
    )
    return {
        "ok": True,
        "message": f"已保存 API Key「{data.label}」",
        "catalog": model_catalog_mod.mask_catalog_for_client(catalog),
    }



@router.post("/model-catalog/delete-provider")
async def delete_catalog_provider(
    data: dict,
    request: Request,
    current_user: Annotated[UserRead, Depends(require_admin)],
    repo: Annotated[SettingRepository, Depends(get_setting_repo)],
):
    """删除已配置供应商（Hermes disconnect）。body: {provider_id}"""
    from fastapi import HTTPException

    pid = str((data or {}).get("provider_id") or "").strip()
    if not pid:
        raise HTTPException(status_code=400, detail="provider_id 不能为空")
    catalog = await model_catalog_mod.load_catalog(repo)
    try:
        catalog = model_catalog_mod.delete_provider(catalog, pid)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    await model_catalog_mod.save_catalog(repo, catalog)
    if catalog.get("active_provider_id"):
        model_catalog_mod.apply_active_to_runtime(catalog)
        active = next(
            (p for p in catalog["providers"] if p["id"] == catalog["active_provider_id"]),
            None,
        )
        if active:
            await repo.upsert(
                "llm_provider", active.get("llm_provider") or "openai-compatible", "llm"
            )
            await repo.upsert("llm_base_url", active.get("llm_base_url") or "", "llm")
            await repo.upsert("llm_model", catalog.get("active_model") or "", "llm")
            key = active.get("llm_api_key") or ""
            if key and "..." not in key:
                await repo.upsert("llm_api_key", key, "llm")
    await log_action(
        AuditAction.SETTINGS_UPDATE,
        request=request,
        user_id=current_user.id,
        resource_type="model_catalog",
        resource_id=pid,
        details={"action": "delete_provider"},
    )
    _notify_settings_changed(current_user.id, ["active_provider_id", "active_model", "llm_model"])
    return {
        "ok": True,
        "message": f"已删除供应商 {pid}",
        "catalog": model_catalog_mod.mask_catalog_for_client(catalog),
        "active_provider_id": catalog.get("active_provider_id") or "",
        "active_model": catalog.get("active_model") or "",
    }


@router.post("/model-catalog/select-credential")
async def select_provider_credential(
    data: SelectCredentialBody,
    request: Request,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[SettingRepository, Depends(get_setting_repo)],
):
    """切换同一供应商下当前使用的 API Key。"""
    catalog = await model_catalog_mod.load_catalog(repo)
    try:
        catalog = model_catalog_mod.set_active_credential(
            catalog, data.provider_id, data.credential_id
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    # 若该供应商是当前激活供应商，立即切到该 Key
    if catalog.get("active_provider_id") == data.provider_id:
        catalog["active_provider_id"] = data.provider_id
        await model_catalog_mod.save_catalog(repo, catalog)
        model_catalog_mod.apply_active_to_runtime(catalog)
        p = next((x for x in catalog["providers"] if x["id"] == data.provider_id), None)
        if p and p.get("llm_api_key"):
            await repo.upsert("llm_api_key", p["llm_api_key"], "llm")
    else:
        await model_catalog_mod.save_catalog(repo, catalog)
    await log_action(
        AuditAction.SETTINGS_UPDATE,
        request=request,
        user_id=current_user.id,
        resource_type="model_catalog",
        resource_id=f"{data.provider_id}/{data.credential_id}",
        details={"action": "select_credential"},
    )
    return {
        "ok": True,
        "message": "已切换 API Key",
        "catalog": model_catalog_mod.mask_catalog_for_client(catalog),
    }


@router.post("/model-catalog/delete-credential")
async def delete_provider_credential(
    data: DeleteCredentialBody,
    request: Request,
    current_user: Annotated[UserRead, Depends(require_admin)],
    repo: Annotated[SettingRepository, Depends(get_setting_repo)],
):
    catalog = await model_catalog_mod.load_catalog(repo)
    try:
        catalog = model_catalog_mod.delete_credential(
            catalog, data.provider_id, data.credential_id
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    await model_catalog_mod.save_catalog(repo, catalog)
    if catalog.get("active_provider_id") == data.provider_id:
        model_catalog_mod.apply_active_to_runtime(catalog)
    await log_action(
        AuditAction.SETTINGS_UPDATE,
        request=request,
        user_id=current_user.id,
        resource_type="model_catalog",
        resource_id=f"{data.provider_id}/{data.credential_id}",
        details={"action": "delete_credential"},
    )
    return {
        "ok": True,
        "message": "已删除 API Key",
        "catalog": model_catalog_mod.mask_catalog_for_client(catalog),
    }


@router.get("", response_model=list[SettingRead])
async def list_settings(
    current_user: Annotated[UserRead, Depends(require_admin)],
    repo: Annotated[SettingRepository, Depends(get_setting_repo)],
    category: str = "",
):
    """列出配置项（仅管理员）"""
    if category:
        return await repo.list_by_category(category) or []
    return await repo.list_all() or []


@router.post("/apply")
async def apply_settings_batch(
    data: BatchSettingsBody,
    request: Request,
    current_user: Annotated[UserRead, Depends(require_admin)],
    repo: Annotated[SettingRepository, Depends(get_setting_repo)],
):
    """
    一键应用多条配置（对标 hermes model / openclaw onboard 的「选完就好」）。
    会写入 DB 并立即刷新内存中的 LLM/Embedding 工厂。
    """
    if not data.items:
        raise HTTPException(status_code=400, detail="items 不能为空")

    CATEGORY_OF = {
        "llm_provider": "llm",
        "llm_model": "llm",
        "llm_base_url": "llm",
        "llm_api_key": "llm",
        "default_llm_model": "llm",
        "max_tokens": "llm",
        "context_window": "llm",
        "temperature": "llm",
        "reasoning_effort": "llm",
        "llm_model_gen_params": "llm",
        "embedding_provider": "embedding",
        "embedding_model": "embedding",
        "embedding_base_url": "embedding",
        "embedding_api_key": "embedding",
        "reranker_provider": "reranker",
        "reranker_model": "reranker",
        "reranker_base_url": "reranker",
        "reranker_api_key": "reranker",
        "image_provider": "image",
        "image_model": "image",
        "image_base_url": "image",
        "image_api_key": "image",
        "rag_enabled": "rag",
                "qdrant_url": "rag",
                "qdrant_collection": "rag",
                "system_name": "general",
                "sft_usage_log_enabled": "privacy",
                "sft_usage_log_path": "privacy",
            }

    # 元数据字段只用于登记目录，不落库为独立 setting
    META_SKIP = {
        "provider_catalog_id",
        "provider_catalog_name",
        "provider_catalog_icon",
        "add_as_new_credential",
        "credential_label",
        "credential_id",
    }

    saved_keys: list[str] = []
    for key, value in data.items.items():
        if key in META_SKIP:
            continue
        # 跳过脱敏占位，避免把 sk-xx...yy 写回覆盖真 Key
        if key.endswith("_api_key") and isinstance(value, str):
            if not value or "..." in value or value == "***":
                continue
        cat = CATEGORY_OF.get(key, "general")
        await repo.upsert(key=key, value=value, category=cat)
        saved_keys.append(key)

    # 若本次没带 api_key，从已有 setting 补全，避免目录登记丢密钥
    if "llm_api_key" not in saved_keys:
        existing_key = await repo.get_by_key("llm_api_key")
        if existing_key and existing_key.value:
            data.items["llm_api_key"] = existing_key.value

    applied = apply_settings_dict(
        {k: data.items[k] for k in saved_keys if not (
            k.endswith("_api_key") and isinstance(data.items.get(k), str)
            and ("..." in str(data.items[k]) or data.items[k] == "***")
        )},
        reset=True,
    )


    # 生成参数绑定到当前激活模型（温度/max_tokens/context_window/思考强度）
    gen_keys = {"temperature", "max_tokens", "context_window", "reasoning_effort"}
    if gen_keys & set(saved_keys):
        try:
            from backend.core import model_catalog as model_catalog_mod
            from backend.core import model_gen_params as gen_params_mod

            cat = await model_catalog_mod.load_catalog(repo)
            pid = str(cat.get("active_provider_id") or "").strip()
            mid = str(cat.get("active_model") or data.items.get("llm_model") or "").strip()
            if mid:
                payload = {}
                if "temperature" in saved_keys:
                    payload["temperature"] = data.items.get("temperature")
                if "max_tokens" in saved_keys:
                    payload["max_tokens"] = data.items.get("max_tokens")
                if "context_window" in saved_keys:
                    payload["context_window"] = data.items.get("context_window")
                if "reasoning_effort" in saved_keys:
                    payload["reasoning_effort"] = data.items.get("reasoning_effort")
                await gen_params_mod.upsert_params(repo, pid, mid, payload)
                saved_keys.append(gen_params_mod.SETTING_KEY)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("bind gen params to model failed: %s", e)

    # 同步登记到多供应商目录（对话页下拉可选）
    try:
        from backend.core import model_catalog as model_catalog_mod

        items = data.items
        if any(k.startswith("llm_") for k in items):
            provider_type = str(items.get("llm_provider") or "")
            base_url = str(items.get("llm_base_url") or "")
            model = str(items.get("llm_model") or "")
            api_key = items.get("llm_api_key")
            if isinstance(api_key, str) and ("..." in api_key or api_key == "***"):
                api_key = None
            pid = str(items.get("provider_catalog_id") or "")
            pname = str(items.get("provider_catalog_name") or "")
            picon = str(items.get("provider_catalog_icon") or "🤖")
            base_l = base_url.lower()
            if not pid:
                if "deepseek" in base_l:
                    pid, pname = "deepseek", pname or "DeepSeek"
                elif "dashscope" in base_l or "aliyun" in base_l:
                    pid, pname = "qwen", pname or "通义千问"
                elif "moonshot" in base_l:
                    pid, pname = "moonshot", pname or "Kimi"
                elif "bigmodel" in base_l:
                    pid, pname = "zhipu", pname or "智谱 GLM"
                elif "openrouter" in base_l:
                    pid, pname = "openrouter", pname or "OpenRouter"
                elif "opencode.ai/zen/go" in base_l:
                    pid, pname = "opencode-go", pname or "OpenCode Go"
                elif "opencode.ai/zen" in base_l:
                    pid, pname = "opencode-zen", pname or "OpenCode Zen"
                elif provider_type == "ollama":
                    pid, pname = "ollama", pname or "本地运行"
                elif provider_type == "openai":
                    pid, pname = "openai", pname or "OpenAI"
                elif provider_type == "anthropic":
                    pid, pname = "anthropic", pname or "Claude"
                else:
                    pid = (
                        provider_type
                        if provider_type and provider_type != "openai-compatible"
                        else ""
                    )
                    pname = pname or pid

            catalog = await model_catalog_mod.load_catalog(repo)
            catalog = model_catalog_mod.prune_orphan_providers(catalog)

            if (not pid or pid == "custom") and base_url:
                matched = model_catalog_mod.match_provider_id_by_base_url(catalog, base_url)
                if matched:
                    pid = matched
                    hit = next(
                        (x for x in catalog["providers"] if x.get("id") == matched), None
                    )
                    if hit:
                        pname = pname or str(hit.get("name") or matched)
            if not pid:
                pid = "custom" if base_url else ""

            if (not base_url and provider_type != "ollama") or not pid:
                raise RuntimeError("skip catalog upsert: missing base_url/provider_id")

            add_new = str(items.get("add_as_new_credential") or "").lower() in {
                "1",
                "true",
                "yes",
            }
            cred_label = str(items.get("credential_label") or "").strip() or None
            catalog = model_catalog_mod.upsert_provider(
                catalog,
                provider_id=pid,
                name=pname or pid,
                llm_provider=provider_type or "openai-compatible",
                llm_base_url=base_url,
                llm_api_key=str(api_key) if api_key else None,
                icon=picon,
                preset_id=pid,
                set_active=True,
                active_model=model or None,
                credential_label=cred_label,
                add_as_new_credential=add_new,
            )
            catalog = model_catalog_mod.prune_orphan_providers(catalog)
            await model_catalog_mod.save_catalog(repo, catalog)
    except Exception as e:
        # 目录登记失败不影响主配置
        import logging

        logging.getLogger(__name__).warning("model catalog upsert skipped: %s", e)

    await log_action(
        AuditAction.SETTINGS_UPDATE,
        request=request,
        user_id=current_user.id,
        resource_type="setting",
        resource_id="batch",
        details={"keys": saved_keys, "applied": applied},
    )
    _notify_settings_changed(current_user.id, saved_keys)
    return {
        "ok": True,
        "saved": saved_keys,
        "applied": applied,
        "message": "配置已保存并生效，可在对话页切换供应商/模型",
    }


@router.post("/test-llm")
async def test_llm_connection(
    data: TestLLMBody,
    current_user: Annotated[UserRead, Depends(require_admin)],
    repo: Annotated[SettingRepository, Depends(get_setting_repo)],
):
    """
    验证 API 可连通，并实时拉取供应商模型列表（非 mock）。
    不修改持久化配置。
    """
    import aiohttp

    from backend.core.config import settings

    provider = data.llm_provider or settings.llm_provider
    base_url = (data.llm_base_url or settings.llm_base_url or "").rstrip("/")
    model = data.llm_model or settings.llm_model
    api_key = await _resolve_api_key(repo, data.llm_api_key)

    # 先拉模型列表：对 Ollama 这本身就是连通性证明
    listed = await fetch_provider_models(provider, base_url, api_key)
    available = listed.get("models") or []

    if provider == "ollama":
        if not listed.get("ok"):
            return {
                "ok": False,
                "message": listed.get("message") or "Ollama 未就绪",
                "detail": listed.get("detail"),
                "available": [],
            }
        if available and model:
            has_model = any(model == n or n.startswith(model + ":") or model in n for n in available)
            if not has_model:
                return {
                    "ok": True,
                    "message": f"已连上 Ollama（{len(available)} 个模型）。当前模型「{model}」不在列表中，请从下方选择或 ollama pull。",
                    "available": available,
                    "models": available,
                }
        return {
            "ok": True,
            "message": f"Ollama 正常 · 已拉取 {len(available)} 个模型",
            "available": available,
            "models": available,
        }

    # 云端 / 兼容端：若能拉到模型列表，即视为连接成功
    if listed.get("ok") and available:
        return {
            "ok": True,
            "message": f"连接成功 · 已从供应商拉取 {len(available)} 个模型",
            "available": available,
            "models": available,
        }

    # 部分供应商不提供 /models：再发一条极短 chat 验证
    try:
        if provider == "anthropic":
            url = f"{base_url}/v1/messages"
            headers = {
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
                "x-api-key": api_key or "",
            }
            payload = {
                "model": model or "claude-3-5-haiku-latest",
                "max_tokens": 16,
                "messages": [{"role": "user", "content": "hi"}],
            }
        else:
            import re as _re
            if _re.search(r"/(v\d+|api)$", base_url):
                url = f"{base_url}/chat/completions"
            else:
                url = f"{base_url}/v1/chat/completions"
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            payload = {
                "model": model or "default",
                "max_tokens": 16,
                "messages": [{"role": "user", "content": "hi"}],
            }

        if not api_key and provider not in ("vllm",):
            if "localhost" not in base_url and "127.0.0.1" not in base_url:
                return {
                    "ok": False,
                    "message": "请先填写 API Key",
                    "available": [],
                    "models": [],
                }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=20)
            ) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    hint = listed.get("message") or "请检查 API Key、模型名与服务地址"
                    if resp.status in (401, 403):
                        hint = "认证失败：API Key 无效或权限不足"
                    return {
                        "ok": False,
                        "message": f"连接失败 (HTTP {resp.status})。{hint}",
                        "detail": text[:300],
                        "available": [],
                        "models": [],
                    }
                return {
                    "ok": True,
                    "message": "连接成功（该供应商未提供模型列表接口，请手动填写模型名）",
                    "available": available,
                    "models": available,
                }
    except aiohttp.ClientConnectorError:
        return {
            "ok": False,
            "message": f"无法连接到 {base_url}。请确认服务已启动、地址正确，或检查网络/代理。",
            "available": [],
            "models": [],
        }
    except TimeoutError:
        return {
            "ok": False,
            "message": "连接超时。服务响应过慢或不可达。",
            "available": [],
            "models": [],
        }
    except Exception as e:
        return {
            "ok": False,
            "message": listed.get("message") or f"测试失败：{e}",
            "available": [],
            "models": [],
        }



@router.get("/sft-corpus")
async def get_sft_corpus_info(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[SettingRepository, Depends(get_setting_repo)],
):
    """使用日志 / SFT 语料收集状态与本地路径。"""
    from backend.services.sft_collector import (
        SETTING_KEY,
        corpus_path_display,
        is_enabled,
    )

    row = await repo.get_by_key(SETTING_KEY)
    raw = None
    if row is not None:
        raw = getattr(row, "value", row)
    enabled = await is_enabled()
    root = corpus_path_display()
    files: list[str] = []
    try:
        from pathlib import Path

        p = Path(root)
        if p.is_dir():
            files = sorted([f.name for f in p.glob("sft_*.md")] + [f.name for f in p.glob("sft_*.jsonl")])[
                -30:
            ]
    except Exception:
        files = []
    return {
        "enabled": enabled,
        "setting_key": SETTING_KEY,
        "path": root,
        "files": files,
        "stored_value": raw,
        "help": (
            f"此功能开启后，Agent 将会自动收集用户指令和运行轨迹数据，"
            f"所有数据均将以 SFT 语料（Markdown + JSONL）的形式存在本地路径：{root}"
        ),
    }



# ⚠️ 以下所有单段静态路径必须声明在 /{key} 通配之前 —— FastAPI 按声明顺序匹配。
# /rag-status 此前排在 /{key} 后面，被通配吞掉，恒定返回 404「Setting not found」，
# 而且要求 require_admin 而非本意的 get_current_user。
# （两段路径如 /security/* 不受影响：路径参数默认不匹配 '/'。）

@router.get("/rag-status")
async def rag_capability_status(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """向量 RAG 能力状态：local（默认）/ full。Reranker 仅作增强项展示。"""
    from backend.services.rag.capability import get_rag_status
    from backend.services.rag.factory import RAGServiceFactory

    # 设置变更后调用方可 force；此处读缓存
    st = get_rag_status(force=True)
    RAGServiceFactory.reset()  # 下次 get_service 按新能力重建
    return st.to_dict()


@router.get("/{key}", response_model=SettingRead)
async def get_setting(
    key: str,
    current_user: Annotated[UserRead, Depends(require_admin)],
    repo: Annotated[SettingRepository, Depends(get_setting_repo)],
):
    """获取单个配置（仅管理员）

    DB 无记录时：若 key 属于运行时映射（如 single_user_mode），
    回落内存 Settings 默认值，避免设置页首启 404。
    """
    setting = await repo.get_by_key(key)
    if setting is not None:
        return setting

    from backend.core.runtime_settings import (
        is_runtime_mapped_key,
        peek_runtime_setting,
    )

    if is_runtime_mapped_key(key):
        raw = peek_runtime_setting(key)
        # 与 updateSetting 写入形态对齐：bool → "true"/"false" 字符串，便于前端 parseBool
        if isinstance(raw, bool):
            value: Any = "true" if raw else "false"
        else:
            value = raw
        security_keys = {
            "single_user_mode",
            "agent_computer_enabled",
            "bridge_token",
            "agent_working_mode",
            "agent_execution_mode",
            "agent_permission_headless",
            "agent_permission_profile",
            "agent_permission_ask_mode",
        }
        return SettingRead(
            key=key,
            value=value,
            category="security" if key in security_keys else "general",
            description=f"Runtime default ({key})",
            updated_at=datetime.now(timezone.utc),
        )

    raise HTTPException(status_code=404, detail="Setting not found")


@router.put("/{key}", response_model=SettingRead)
async def upsert_setting(
    key: str,
    data: SettingUpdate,
    request: Request,
    current_user: Annotated[UserRead, Depends(require_admin)],
    repo: Annotated[SettingRepository, Depends(get_setting_repo)],
):
    """创建或更新配置（仅管理员），并立即应用到运行时"""
    # 避免前端把脱敏后的 api_key 写回
    if key.endswith("_api_key") and isinstance(data.value, str):
        if not data.value or "..." in data.value or data.value == "***":
            existing = await repo.get_by_key(key)
            if existing is None:
                raise HTTPException(status_code=400, detail="API Key 不能为空")
            return existing

    setting = await repo.upsert(
        key=key,
        value=data.value,
        category=data.category or "general",
        description=data.description,
    )
    if key == "sft_usage_log_enabled":
        try:
            from backend.services.sft_collector import invalidate_enabled_cache

            invalidate_enabled_cache()
        except Exception:
            pass
    if key == "command_security_policy":
        try:
            from backend.core.command_policy import invalidate_command_policy_cache

            invalidate_command_policy_cache()
        except Exception:
            pass
    # upsert 已返回明文；再保险一次
    plain = decrypt_setting(setting.value, key=key) if isinstance(setting.value, str) else data.value
    if apply_setting_value(key, plain):
        reset_factories_for_keys([key])

    await log_action(
        AuditAction.SETTINGS_UPDATE,
        request=request,
        user_id=current_user.id,
        resource_type="setting",
        resource_id=key,
        details={"category": data.category or "general"},
    )
    # 返回给前端前解密（SettingRead 会对 api_key 再脱敏）
    if isinstance(setting.value, str):
        setting.value = decrypt_setting(setting.value, key=key)
    return setting


@router.delete("/{key}")
async def delete_setting(
    key: str,
    current_user: Annotated[UserRead, Depends(require_admin)],
    repo: Annotated[SettingRepository, Depends(get_setting_repo)],
):
    """删除配置（仅管理员）"""
    success = await repo.delete(key)
    if not success:
        raise HTTPException(status_code=404, detail="Setting not found")
    # 内存同步：已知 runtime key 重置为字段默认值（避免 DB 删了内存还留着）
    from backend.core.runtime_settings import clear_setting_value

    clear_setting_value(key)
    return {"deleted": True}

# ---- 安全加固（2026-07-26）：自检报告 + 桥接令牌生成 ----
# 注意路径均为两段（/security/*），不会被上方 /{key} 通配吞掉。


@router.get("/security/audit")
async def get_security_audit(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """复跑统一安全自检，返回结构化结果供权限控制台展示。"""
    from backend.core.security_check import collect_security_report

    report = collect_security_report()
    return {
        "worst": report.worst,
        "results": [
            {"id": r.id, "level": r.level, "message": r.message, "hint": r.hint}
            for r in report.results
        ],
    }


@router.get("/security/command-policy")
async def get_command_policy(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """权限控制台：高危命令分类元数据 + 当前三态动作。"""
    from backend.core.command_policy import policy_payload

    return await policy_payload()


@router.get("/security/working-mode")
async def get_working_mode(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """权限控制台：当前工作方式 / 执行环境 + 实际生效值 + 可选项目录（T5）。

    返回 effective.* 是刻意的：用户选的和真正生效的可能不一致
    （本机无沙箱 → auto 退回本机；高级用户单独覆盖了底层键），必须让用户看得见。
    """
    from backend.agent.working_mode import describe_current

    return describe_current()


class WorkingModeUpdate(BaseModel):
    working_mode: Optional[str] = None
    execution_mode: Optional[str] = None


@router.post("/security/working-mode")
async def set_working_mode(
    payload: WorkingModeUpdate,
    request: Request,
    current_user: Annotated[UserRead, Depends(require_admin)],
    repo: Annotated[SettingRepository, Depends(get_setting_repo)],
):
    """切换工作方式 / 执行环境（实时生效 + DB 持久化）。"""
    from backend.agent.working_mode import (
        EXECUTION_MODE_BY_ID,
        WORKING_MODE_BY_ID,
        describe_current,
    )

    changed: dict[str, str] = {}

    if payload.working_mode is not None:
        wm = str(payload.working_mode).strip().lower()
        if wm not in WORKING_MODE_BY_ID:
            raise HTTPException(
                status_code=400,
                detail=f"未知工作方式 '{wm}'，可选: {', '.join(WORKING_MODE_BY_ID)}",
            )
        changed["agent_working_mode"] = wm

    if payload.execution_mode is not None:
        em = str(payload.execution_mode).strip().lower()
        if em not in EXECUTION_MODE_BY_ID:
            raise HTTPException(
                status_code=400,
                detail=f"未知执行环境 '{em}'，可选: {', '.join(EXECUTION_MODE_BY_ID)}",
            )
        changed["agent_execution_mode"] = em
        # 兼容键同步：旧 agent_computer_enabled 仍被旧前端/旧配置读写，
        # 不同步会出现「控制台显示本机、实则强制沙箱」的错位。
        changed["agent_computer_enabled"] = "true" if em == "sandbox" else "false"

    if not changed:
        raise HTTPException(status_code=400, detail="未提供任何变更")

    for key, value in changed.items():
        await repo.upsert(
            key=key,
            value=value,
            category="security",
            description="权限控制台：工作方式 / 执行环境",
        )
        apply_setting_value(key, value)

    await log_action(
        AuditAction.SETTINGS_UPDATE,
        request=request,
        user_id=current_user.id,
        resource_type="setting",
        resource_id="working_mode",
        details={"category": "security", "changed": changed},
    )

    return describe_current()


@router.post("/security/generate-bridge-token")
async def generate_bridge_token(
    request: Request,
    current_user: Annotated[UserRead, Depends(require_admin)],
    repo: Annotated[SettingRepository, Depends(get_setting_repo)],
):
    """生成随机 bridge_token 并立即生效（DB 持久化 + 内存应用）。token 仅此一次完整返回。"""
    import secrets as _secrets

    token = _secrets.token_urlsafe(24)
    await repo.upsert(
        key="bridge_token",
        value=token,
        category="security",
        description="Takton Code ↔ Desktop 桥接令牌（设置页生成）",
    )
    apply_setting_value("bridge_token", token)
    await log_action(
        AuditAction.SETTINGS_UPDATE,
        request=request,
        user_id=current_user.id,
        resource_type="setting",
        resource_id="bridge_token",
        details={"category": "security", "action": "generate"},
    )
    return {"bridge_token": token}
