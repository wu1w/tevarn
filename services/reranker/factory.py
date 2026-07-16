"""
Reranker 服务工厂
根据配置自动选择本地 / Cohere 后端
"""

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
        """获取 Reranker 服务单例"""
        if cls._instance is None:
            cls._instance = cls._create_service()
        return cls._instance

    @classmethod
    def _create_service(cls) -> RerankerService:
        """根据 RERANKER_PROVIDER 配置创建对应服务"""
        provider = settings.reranker_provider

        if provider == "local":
            logger.info(f"Using local reranker: {settings.reranker_base_url}/{settings.reranker_model}")
            return LocalRerankerService()
        elif provider == "cohere":
            logger.info(f"Using Cohere reranker: {settings.reranker_base_url}/{settings.reranker_model}")
            return CohereRerankerService()
        else:
            logger.warning(f"Unknown reranker provider '{provider}', falling back to local")
            return LocalRerankerService()

    @classmethod
    def reset(cls) -> None:
        """重置单例（主要用于测试）"""
        cls._instance = None
