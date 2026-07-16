"""
Embedding 服务工厂
根据配置自动选择 Ollama / OpenAI / 本地兼容后端
"""

import logging

from backend.core.config import settings

from .interface import EmbeddingService
from .local_compatible import LocalEmbeddingService
from .ollama import NoOpEmbeddingService, OllamaEmbeddingService
from .openai import OpenAIEmbeddingService

logger = logging.getLogger(__name__)


class EmbeddingServiceFactory:
    """Embedding 服务工厂类"""

    _instance: EmbeddingService | None = None

    @classmethod
    def get_service(cls) -> EmbeddingService:
        """获取 Embedding 服务单例"""
        if cls._instance is None:
            cls._instance = cls._create_service()
        return cls._instance

    @classmethod
    def _create_service(cls) -> EmbeddingService:
        """根据 EMBEDDING_PROVIDER 配置创建对应服务"""
        provider = settings.embedding_provider

        if provider == "ollama":
            svc = OllamaEmbeddingService()
            # 健康检查：如果 Ollama 根本连不上，用 NoOp 兜底，避免每次请求都报错
            try:
                import asyncio
                asyncio.get_event_loop().run_until_complete(svc.embed(["ping"]))
                logger.info(f"Using Ollama embedding: {settings.embedding_base_url}/{settings.embedding_model}")
                return svc
            except Exception as e:
                logger.warning(f"Ollama embedding not reachable ({e}), using no-op fallback")
                return NoOpEmbeddingService()
        elif provider == "openai":
            logger.info(f"Using OpenAI embedding: {settings.embedding_base_url}/{settings.embedding_model}")
            return OpenAIEmbeddingService()
        elif provider == "openai-compatible":
            logger.info(f"Using OpenAI-Compatible embedding: {settings.embedding_base_url}/{settings.embedding_model}")
            return LocalEmbeddingService()
        else:
            logger.warning(f"Unknown embedding provider '{provider}', falling back to Ollama")
            return OllamaEmbeddingService()

    @classmethod
    def reload(cls) -> EmbeddingService:
        """重载 Embedding 服务（配置变更后调用）"""
        cls._instance = None
        new_svc = cls.get_service()
        logger.info("EmbeddingServiceFactory reloaded — new instance created")
        return new_svc

    @classmethod
    def reset(cls) -> None:
        """重置单例（主要用于测试）"""
        cls._instance = None
