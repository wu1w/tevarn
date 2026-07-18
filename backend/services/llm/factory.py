"""
LLM 服务工厂
根据配置自动选择 Ollama / vLLM / OpenAI / Anthropic / OpenAI-Compatible 后端
"""

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

        用于会话锁定创建时的模型/provider：进行中/历史会话的 LLM call
        使用其创建时快照的配置，不受全局配置变更影响。
        snapshot 缺字段或为 None 时 fallback 到全局单例。
        """
        if not snapshot or not snapshot.get("provider"):
            return cls.get_service()
        provider = snapshot["provider"]
        # 构造最小 config 对象（对齐各 Service 读取的字段）
        from types import SimpleNamespace

        base_cfg = settings.get_llm_config()
        def _pick(name, default):
            v = snapshot.get(name)
            return default if v is None else v

        config = SimpleNamespace(
            base_url=(snapshot.get("base_url") or getattr(base_cfg, "base_url", "") or "").rstrip("/"),
            model=snapshot.get("model") or getattr(base_cfg, "model", "") or "",
            api_key=snapshot.get("api_key") if snapshot.get("api_key") is not None else getattr(base_cfg, "api_key", None),
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
