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
