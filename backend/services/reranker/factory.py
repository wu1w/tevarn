"""
Reranker 服务工厂
"""

from __future__ import annotations

import logging

from backend.core.config import settings

from .cohere import CohereRerankerService
from .interface import RerankerService
from .local import LocalRerankerService

logger = logging.getLogger(__name__)


class RerankerServiceFactory:
    """Reranker 服务工厂类"""

    _instance: RerankerService | None = None

    @classmethod
    def get_service(cls) -> RerankerService:
        if cls._instance is None:
            cls._instance = cls._create_service()
        return cls._instance

    @classmethod
    def _create_service(cls) -> RerankerService:
        provider = (settings.reranker_provider or "").strip().lower()
        base = (settings.reranker_base_url or "").strip()

        if not provider or not base:
            logger.info("Reranker not configured, using local service with soft fallback")
            return LocalRerankerService()

        if provider == "cohere":
            logger.info("Using Cohere reranker: %s/%s", base, settings.reranker_model)
            return CohereRerankerService()

        # local / openai-compatible / jina / siliconflow / tei / llamacpp 均走自适应本地实现
        if provider in (
            "local",
            "openai-compatible",
            "jina",
            "siliconflow",
            "tei",
            "llamacpp",
            "vllm",
            "xinference",
        ):
            logger.info("Using adaptive reranker (%s): %s/%s", provider, base, settings.reranker_model)
            return LocalRerankerService()

        logger.warning("Unknown reranker provider %r, using adaptive local", provider)
        return LocalRerankerService()

    @classmethod
    def reset(cls) -> None:
        cls._instance = None
