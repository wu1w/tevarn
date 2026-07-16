"""
RAG 服务工厂
支持通过配置注入自定义 RAG 实现
"""

import importlib
import logging

from backend.core.config import settings

from .interface import RAGService
from .qdrant_impl import QdrantRAGService

logger = logging.getLogger(__name__)


class RAGServiceFactory:
    """RAG 服务工厂类"""

    _instance: RAGService | None = None

    @classmethod
    def get_service(cls) -> RAGService:
        """获取 RAG 服务单例"""
        if cls._instance is None:
            cls._instance = cls._create_service()
        return cls._instance

    @classmethod
    def _create_service(cls) -> RAGService:
        """根据配置创建 RAG 服务实例"""
        class_path = settings.rag_service_class

        if class_path == "backend.services.rag.qdrant_impl.QdrantRAGService":
            logger.info("Using QdrantRAGService")
            return QdrantRAGService()

        try:
            module_path, class_name = class_path.rsplit(".", 1)
            module = importlib.import_module(module_path)
            service_class = getattr(module, class_name)

            if not issubclass(service_class, RAGService):
                raise TypeError(
                    f"{class_path} must inherit from RAGService"
                )

            logger.info(f"Using custom RAG service: {class_path}")
            return service_class()
        except Exception as e:
            logger.error(
                f"Failed to load RAG service {class_path}: {e}. "
                "Falling back to QdrantRAGService."
            )
            return QdrantRAGService()

    @classmethod
    def reset(cls) -> None:
        """重置单例（主要用于测试）"""
        cls._instance = None
