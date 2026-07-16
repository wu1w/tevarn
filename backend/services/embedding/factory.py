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
        provider = (settings.embedding_provider or "").strip().lower()
        base = (settings.embedding_base_url or "").strip()

        # 未配置时不要默认打 Ollama
        if not provider or not base:
            logger.warning(
                "Embedding not configured (provider=%r base=%r), using no-op",
                provider,
                base,
            )
            return NoOpEmbeddingService()

        if provider == "ollama":
            svc = OllamaEmbeddingService()
            # 健康检查：如果 Ollama 根本连不上，用 NoOp 兜底，避免每次请求都报错
            try:
                import asyncio

                asyncio.get_event_loop().run_until_complete(svc.embed(["ping"]))
                logger.info(
                    "Using Ollama embedding: %s/%s",
                    settings.embedding_base_url,
                    settings.embedding_model,
                )
                return svc
            except Exception as e:
                logger.warning("Ollama embedding not reachable (%s), using no-op fallback", e)
                return NoOpEmbeddingService()
        elif provider == "openai":
            logger.info(
                "Using OpenAI embedding: %s/%s",
                settings.embedding_base_url,
                settings.embedding_model,
            )
            return OpenAIEmbeddingService()
        elif provider in ("openai-compatible", "vllm", "llamacpp", "tei", "local"):
            logger.info(
                "Using OpenAI-Compatible embedding: %s/%s",
                settings.embedding_base_url,
                settings.embedding_model,
            )
            return LocalEmbeddingService()
        else:
            # 未知 provider：有 base_url 则按兼容接口，不再误走 Ollama
            logger.warning(
                "Unknown embedding provider %r, treating as openai-compatible",
                provider,
            )
            return LocalEmbeddingService()

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
