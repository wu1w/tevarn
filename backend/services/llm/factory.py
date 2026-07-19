"""
LLM 服务工厂
根据配置自动选择 Ollama / vLLM / OpenAI / Anthropic / OpenAI-Compatible 后端
"""

from __future__ import annotations

import logging

from backend.core.config import settings

from .anthropic import AnthropicService
from .interface import LLMService
from .ollama import OllamaService
from .openai_cloud import OpenAIService
from .openai_compatible import OpenAICompatibleService
from .vllm import VLLMService

logger = logging.getLogger(__name__)


class LLMServiceFactory:
    """LLM 服务工厂类"""

    _instance: LLMService | None = None

    @classmethod
    def get_service(cls) -> LLMService:
        """获取 LLM 服务单例"""
        if cls._instance is None:
            cls._instance = cls._create_service()
        return cls._instance

    @classmethod
    def _create_service(cls) -> LLMService:
        """根据 LLM_PROVIDER 配置创建对应服务"""
        provider = settings.llm_provider
        config = settings.get_llm_config()

        if provider == "ollama":
            logger.info(f"Using Ollama backend: {config.base_url}/{config.model}")
            return OllamaService(config)
        elif provider == "vllm":
            logger.info(f"Using vLLM backend: {config.base_url}/{config.model}")
            return VLLMService(config)
        elif provider == "openai":
            logger.info(f"Using OpenAI backend: {config.base_url}/{config.model}")
            return OpenAIService(config)
        elif provider == "anthropic":
            logger.info(f"Using Anthropic backend: {config.base_url}/{config.model}")
            return AnthropicService(config)
        elif provider == "openai-compatible":
            logger.info(f"Using OpenAI-Compatible backend: {config.base_url}/{config.model}")
            return OpenAICompatibleService(config)
        else:
            raise ValueError(f"Unsupported LLM provider: {provider}")

    @classmethod
    def reset(cls) -> None:
        """重置单例（主要用于测试）"""
        cls._instance = None

    @classmethod
    def get_service_for_snapshot(cls, snapshot: dict | None) -> LLMService:
        """按会话快照创建 LLM 服务（不走全局单例）。

        会话锁定 model/provider；API Key 优先从 model_catalog 按 provider_id
        实时解析，避免设置里已换 Key、旧会话仍 401。
        """
        if not snapshot or not snapshot.get("provider"):
            return cls.get_service()
        provider = snapshot["provider"]
        from types import SimpleNamespace

        base_cfg = settings.get_llm_config()

        def _pick(name, default):
            v = snapshot.get(name)
            return default if v is None else v

        base_url = (snapshot.get("base_url") or getattr(base_cfg, "base_url", "") or "").rstrip("/")
        model = snapshot.get("model") or getattr(base_cfg, "model", "") or ""
        api_key = (
            snapshot.get("api_key")
            if snapshot.get("api_key") is not None
            else getattr(base_cfg, "api_key", None)
        )

        provider_id = str(snapshot.get("provider_id") or "").strip()
        fresh = cls._resolve_live_credentials(provider_id, base_url)
        if fresh:
            if fresh.get("api_key"):
                api_key = fresh["api_key"]
            if fresh.get("base_url"):
                base_url = fresh["base_url"]
            if fresh.get("llm_provider"):
                provider = fresh["llm_provider"] or provider

        if cls._looks_like_placeholder_key(api_key):
            global_key = getattr(base_cfg, "api_key", None) or ""
            if global_key and not cls._looks_like_placeholder_key(global_key):
                api_key = global_key
                logger.warning(
                    "snapshot api_key looks placeholder; using global llm_api_key for provider_id=%s",
                    provider_id or "?",
                )

        config = SimpleNamespace(
            base_url=base_url,
            model=model,
            api_key=api_key,
            max_tokens=int(_pick("max_tokens", getattr(base_cfg, "max_tokens", 4096)) or 4096),
            temperature=float(_pick("temperature", getattr(base_cfg, "temperature", 0.7)) or 0.7),
        )
        if provider == "ollama":
            return OllamaService(config)
        elif provider == "vllm":
            return VLLMService(config)
        elif provider == "openai":
            return OpenAIService(config)
        elif provider == "anthropic":
            return AnthropicService(config)
        elif provider == "openai-compatible":
            return OpenAICompatibleService(config)
        else:
            logger.warning("Unknown snapshot provider %r, fallback to global", provider)
            return cls.get_service()

    @staticmethod
    def _looks_like_placeholder_key(key: object) -> bool:
        s = str(key or "").strip()
        if not s:
            return True
        low = s.lower()
        if low.startswith("sk-test") or low.startswith("test-") or "your-api-key" in low:
            return True
        if s in ("***", "changeme", "placeholder"):
            return True
        return False

    @classmethod
    def _resolve_live_credentials(cls, provider_id: str, base_url: str) -> dict | None:
        """从 model_catalog 取最新 api_key / base_url。"""
        try:
            import asyncio
            import concurrent.futures

            from backend.core import model_catalog as mc
            from backend.repositories.setting_repo import AsyncSettingRepository

            async def _load() -> dict | None:
                repo = AsyncSettingRepository()
                cat = await mc.load_catalog(repo)
                providers = cat.get("providers") or []
                p = None
                if provider_id:
                    p = next((x for x in providers if x.get("id") == provider_id), None)
                if p is None and base_url:
                    bu = base_url.rstrip("/")
                    p = next(
                        (
                            x
                            for x in providers
                            if str(x.get("llm_base_url") or "").rstrip("/") == bu
                        ),
                        None,
                    )
                if p is None and cat.get("active_provider_id"):
                    ap = cat["active_provider_id"]
                    p = next((x for x in providers if x.get("id") == ap), None)
                if not p:
                    return None
                key = mc._active_api_key(p)  # noqa: SLF001
                return {
                    "api_key": key or None,
                    "base_url": (p.get("llm_base_url") or "").rstrip("/") or None,
                    "llm_provider": p.get("llm_provider") or None,
                }

            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running and running.is_running():
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    return pool.submit(lambda: asyncio.run(_load())).result(timeout=5)
            return asyncio.run(_load())
        except Exception as e:
            logger.debug("live credential resolve failed: %s", e)
            return None
