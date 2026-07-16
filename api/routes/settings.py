"""
Settings 路由
运行时配置管理 API —— 对标 Hermes/OpenClaw 的「选服务商 → 填 Key → 能聊天」路径
"""

import uuid
from datetime import datetime, timezone
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from backend.core.audit import AuditAction, log_action
from backend.core.encryption import decrypt_setting
from backend.core import model_catalog as model_catalog_mod
from backend.core.runtime_settings import apply_setting_value, apply_settings_dict, reset_factories_for_keys
from backend.api.websocket import manager as ws_manager
from backend.repositories import SettingRepository
from backend.schemas.setting import SettingRead, SettingUpdate
from backend.schemas.user import UserRead

from ..dependencies import get_current_user, get_setting_repo, require_admin

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
            "llm_model": "gpt-4o-mini",
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
        "description": "国产高性价比模型，中文表现好，兼容 OpenAI 格式。",
        "icon": "🔵",
        "needs_api_key": True,
        "help_url": "https://platform.deepseek.com/api_keys",
        "help_text": "在 DeepSeek 开放平台创建 API Key",
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
        "description": "阿里云 DashScope，兼容 OpenAI 协议。",
        "icon": "🟠",
        "needs_api_key": True,
        "help_url": "https://dashscope.console.aliyun.com/apiKey",
        "help_text": "在阿里云百炼 / DashScope 创建 API Key",
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
        "description": "智谱清言 GLM 系列，兼容 OpenAI 协议。",
        "icon": "🔷",
        "needs_api_key": True,
        "help_url": "https://open.bigmodel.cn/usercenter/apikeys",
        "help_text": "在智谱开放平台创建 API Key",
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
            "llm_base_url": "https://maas-api.cn-huabei-1.xf-yun.com/v2",
            "llm_model": "xop3qwen30b",
            "llm_api_key": "",
        },
        "models": [],
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
        "description": "MiniMax 国际站 OpenAI 兼容接口。",
        "icon": "🟣",
        "needs_api_key": True,
        "help_url": "https://platform.minimax.io/",
        "help_text": "在 MiniMax 国际平台创建 API Key",
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
            "llm_model": "openai/gpt-4o-mini",
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


def _models_url(base_url: str) -> str:
    base = (base_url or "").rstrip("/")
    if base.endswith("/v1") or base.endswith("/v4") or base.endswith("/api"):
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
                        "message": "连接成功，但供应商未返回任何模型",
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

RAG_STACK_PRESETS: list[dict[str, Any]] = [
    {
        "id": "ollama-local",
        "name": "本机 Ollama 全套",
        "badge": "推荐入门",
        "description": "Embedding 用本机 Ollama（nomic-embed-text），向量库用本机 Qdrant，Reranker 用本地。",
        "icon": "💻",
        "help_text": "1) 安装 Ollama 并 pull nomic-embed-text  2) 启动 Qdrant（docker run -p 6333:6333 qdrant/qdrant）  3) 点保存并测试",
        "items": {
            "rag_enabled": True,
            "embedding_provider": "ollama",
            "embedding_base_url": "http://localhost:11434",
            "embedding_model": "nomic-embed-text",
            "embedding_api_key": "",
            "qdrant_url": "http://localhost:6333",
            "qdrant_collection": "knowledge_base",
            "reranker_provider": "local",
            "reranker_base_url": "http://localhost:8001",
            "reranker_model": "bge-reranker-base",
            "reranker_api_key": "",
        },
    },
    {
        "id": "openai-embed",
        "name": "OpenAI Embedding + 本机 Qdrant",
        "badge": "云端向量",
        "description": "用 OpenAI text-embedding-3-small 做向量，Qdrant 仍在本机。",
        "icon": "☁️",
        "help_text": "需要 OpenAI API Key；Qdrant 默认 localhost:6333。",
        "items": {
            "rag_enabled": True,
            "embedding_provider": "openai",
            "embedding_base_url": "https://api.openai.com",
            "embedding_model": "text-embedding-3-small",
            "embedding_api_key": "",
            "qdrant_url": "http://localhost:6333",
            "qdrant_collection": "knowledge_base",
            "reranker_provider": "local",
            "reranker_base_url": "http://localhost:8001",
            "reranker_model": "bge-reranker-base",
        },
    },
    {
        "id": "openai-compatible-embed",
        "name": "兼容接口 Embedding",
        "badge": "自定义",
        "description": "任意 OpenAI 兼容 /v1/embeddings（SiliconFlow、OneAPI、本地 TEI 等）。",
        "icon": "⚙️",
        "help_text": "填写兼容服务的 base_url 与模型名、API Key。",
        "items": {
            "rag_enabled": True,
            "embedding_provider": "openai-compatible",
            "embedding_base_url": "http://127.0.0.1:8080",
            "embedding_model": "bge-m3",
            "embedding_api_key": "",
            "qdrant_url": "http://localhost:6333",
            "qdrant_collection": "knowledge_base",
        },
    },
    {
        "id": "cohere-rerank",
        "name": "Cohere Reranker",
        "badge": "精排",
        "description": "仅切换 Reranker 到 Cohere（保留现有 Embedding/Qdrant）。",
        "icon": "🎯",
        "help_text": "在 Cohere 控制台申请 API Key。",
        "items": {
            "reranker_provider": "cohere",
            "reranker_base_url": "https://api.cohere.ai",
            "reranker_model": "rerank-multilingual-v3.0",
            "reranker_api_key": "",
        },
    },
    {
        "id": "rag-off",
        "name": "关闭自动 RAG",
        "badge": "关闭",
        "description": "禁用会话自动注入知识库（仍可手动调 search_knowledge_base 工具，若服务可用）。",
        "icon": "🚫",
        "help_text": "适合暂时不需要检索、或尚未部署向量库时。",
        "items": {
            "rag_enabled": False,
        },
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
    """探测 Embedding 是否可用。"""
    from backend.core.config import settings as app_settings
    from backend.core.runtime_settings import apply_settings_dict
    from backend.services.embedding.factory import EmbeddingServiceFactory

    items = {k: v for k, v in data.model_dump().items() if v is not None and v != ""}
    if items:
        apply_settings_dict(items, reset=True)
    try:
        EmbeddingServiceFactory.reset()
        svc = EmbeddingServiceFactory.get_service()
        vec = await svc.embed_query("takton embedding ping")
        dim = len(vec) if vec else 0
        if dim <= 0:
            return {"ok": False, "message": "Embedding 返回空向量"}
        return {
            "ok": True,
            "message": f"Embedding 正常 · 维度 {dim} · 模型 {app_settings.embedding_model}",
            "dimension": dim,
            "model": app_settings.embedding_model,
            "provider": app_settings.embedding_provider,
        }
    except Exception as e:
        return {"ok": False, "message": f"Embedding 失败: {e}"}


@router.post("/test-qdrant")
async def test_qdrant(
    data: TestQdrantBody,
    current_user: Annotated[UserRead, Depends(require_admin)],
):
    """探测 Qdrant 连通性。"""
    import aiohttp
    from backend.core.config import settings as app_settings
    from backend.core.runtime_settings import apply_settings_dict

    items = {k: v for k, v in data.model_dump().items() if v is not None and v != ""}
    if items:
        apply_settings_dict(items, reset=True)
    url = (app_settings.qdrant_url or "").rstrip("/")
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as session:
            async with session.get(f"{url}/collections") as resp:
                text = await resp.text()
                if resp.status != 200:
                    return {
                        "ok": False,
                        "message": f"Qdrant HTTP {resp.status}: {text[:200]}",
                    }
                return {
                    "ok": True,
                    "message": f"Qdrant 可达 · {url} · collection 默认 {app_settings.qdrant_collection}",
                    "url": url,
                    "collection": app_settings.qdrant_collection,
                }
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
    """
    from backend.core.config import settings

    provider = data.llm_provider or settings.llm_provider
    base_url = (data.llm_base_url or settings.llm_base_url or "").rstrip("/")
    api_key = await _resolve_api_key(repo, data.llm_api_key)
    return await fetch_provider_models(provider, base_url, api_key)


# ---- 模型目录（Hermes Desktop 风格：多供应商 + 禁用模型 + 对话页切换）----


class SelectModelBody(BaseModel):
    provider_id: str
    model: str


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


@router.get("/model-catalog")
async def get_model_catalog(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[SettingRepository, Depends(get_setting_repo)],
    fetch_models: bool = True,
):
    """
    返回已配置供应商目录。
    fetch_models=true 时为每个启用的供应商实时拉取模型列表，并标注 disabled。
    """
    catalog = await model_catalog_mod.load_catalog(repo)
    # OAuth token 临近过期时刷新
    try:
        catalog = await model_catalog_mod.ensure_oauth_token_fresh(catalog)
        await model_catalog_mod.save_catalog(repo, catalog)
    except Exception:
        pass
    public = model_catalog_mod.mask_catalog_for_client(catalog)

    providers_out: list[dict[str, Any]] = []
    for p in public.get("providers") or []:
        entry = dict(p)
        entry["models"] = []
        entry["fetch_ok"] = None
        entry["fetch_message"] = ""
        if fetch_models and p.get("enabled", True):
            # 用未脱敏的 key 拉取
            raw = next(
                (x for x in catalog["providers"] if x["id"] == p["id"]),
                None,
            )
            api_key = (raw or {}).get("llm_api_key") or ""
            listed = await fetch_provider_models(
                p.get("llm_provider") or "openai-compatible",
                p.get("llm_base_url") or "",
                api_key,
            )
            disabled_set = set(p.get("disabled_models") or [])
            models = []
            for mid in listed.get("models") or []:
                models.append(
                    {
                        "id": mid,
                        "disabled": mid in disabled_set,
                    }
                )
            entry["models"] = models
            entry["fetch_ok"] = bool(listed.get("ok"))
            entry["fetch_message"] = listed.get("message") or ""
        else:
            # 即使不拉取，也把已禁用的模型列出来方便管理
            for mid in p.get("disabled_models") or []:
                entry["models"].append({"id": mid, "disabled": True})
        providers_out.append(entry)

    return {
        "active_provider_id": public.get("active_provider_id") or "",
        "active_model": public.get("active_model") or "",
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
    catalog = await model_catalog_mod.load_catalog(repo)
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

    from backend.core.model_limits import limits_for_model

    lim = limits_for_model(data.model)

    # 同步 llm_* 设置 + 上下文窗口
    await repo.upsert("llm_provider", provider["llm_provider"], "llm")
    await repo.upsert("llm_base_url", provider["llm_base_url"], "llm")
    await repo.upsert("llm_model", data.model, "llm")
    if provider.get("llm_api_key"):
        await repo.upsert("llm_api_key", provider["llm_api_key"], "llm")
    await repo.upsert("context_window", lim["context_window"], "llm")
    await repo.upsert("max_tokens", lim["max_tokens"], "llm")

    await log_action(
        AuditAction.SETTINGS_UPDATE,
        request=request,
        user_id=current_user.id,
        resource_type="model_catalog",
        resource_id=f"{data.provider_id}/{data.model}",
        details={
            "action": "select",
            "context_window": lim["context_window"],
            "max_tokens": lim["max_tokens"],
        },
    )
    _notify_settings_changed(
        current_user.id,
        ["active_provider_id", "active_model", "llm_provider", "llm_model", "llm_base_url"],
    )
    return {
        "ok": True,
        "active_provider_id": data.provider_id,
        "active_model": data.model,
        "provider_name": provider.get("name") or data.provider_id,
        "context_window": lim["context_window"],
        "max_tokens": lim["max_tokens"],
        "message": (
            f"已切换到 {provider.get('name')} / {data.model}"
            f"（上下文 {lim['context_window']//1000}K · 生成上限 {lim['max_tokens']}）"
        ),
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
        "max_tokens": "llm",
        "context_window": "llm",
        "temperature": "llm",
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

    # 同步登记到多供应商目录（对话页下拉可选）
    try:
        items = data.items
        if any(k.startswith("llm_") for k in items):
            provider_type = str(items.get("llm_provider") or "")
            base_url = str(items.get("llm_base_url") or "")
            model = str(items.get("llm_model") or "")
            api_key = items.get("llm_api_key")
            if isinstance(api_key, str) and ("..." in api_key or api_key == "***"):
                api_key = None
            # 推断目录 id / 名称
            pid = str(items.get("provider_catalog_id") or "")
            pname = str(items.get("provider_catalog_name") or "")
            picon = str(items.get("provider_catalog_icon") or "🤖")
            if not pid:
                if "deepseek" in base_url:
                    pid, pname = "deepseek", pname or "DeepSeek"
                elif "dashscope" in base_url or "aliyun" in base_url:
                    pid, pname = "qwen", pname or "通义千问"
                elif "moonshot" in base_url:
                    pid, pname = "moonshot", pname or "Kimi"
                elif "bigmodel" in base_url:
                    pid, pname = "zhipu", pname or "智谱 GLM"
                elif "openrouter" in base_url:
                    pid, pname = "openrouter", pname or "OpenRouter"
                elif provider_type == "ollama":
                    pid, pname = "ollama", pname or "本地运行"
                elif provider_type == "openai":
                    pid, pname = "openai", pname or "OpenAI"
                elif provider_type == "anthropic":
                    pid, pname = "anthropic", pname or "Claude"
                else:
                    pid = provider_type or "custom"
                    pname = pname or pid
            catalog = await model_catalog_mod.load_catalog(repo)
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
            if base_url.endswith("/v1") or base_url.endswith("/v4"):
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


@router.get("/{key}", response_model=SettingRead)
async def get_setting(
    key: str,
    current_user: Annotated[UserRead, Depends(require_admin)],
    repo: Annotated[SettingRepository, Depends(get_setting_repo)],
):
    """获取单个配置（仅管理员）"""
    setting = await repo.get_by_key(key)
    if setting is None:
        raise HTTPException(status_code=404, detail="Setting not found")
    return setting


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
    # upsert 返回的 value 可能仍是密文，解密后再应用
    plain = decrypt_setting(setting.value) if isinstance(setting.value, str) else data.value
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
        setting.value = decrypt_setting(setting.value)
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
    return {"deleted": True}
