"""
RAG 服务工厂
- 默认 / 未配置 Embedding+Qdrant → NullRAGService（本地模式）
- 仅当向量栈就绪时 → QdrantRAGService
"""

from __future__ import annotations

import importlib
import logging

from backend.core.config import settings

from .capability import get_rag_status, invalidate_rag_status_cache, use_vector_rag
from .interface import RAGService
from .null_impl import NullRAGService
from .qdrant_impl import QdrantRAGService

logger = logging.getLogger(__name__)


class RAGServiceFactory:
    """RAG 服务工厂类"""

    _instance: RAGService | None = None
    _instance_kind: str | None = None

    @classmethod
    def get_service(cls) -> RAGService:
        """获取 RAG 服务单例（按能力自动选 Null / 向量实现）"""
        want = "vector" if use_vector_rag() else "null"
        if cls._instance is None or cls._instance_kind != want:
            cls._instance = cls._create_service(want)
            cls._instance_kind = want
        return cls._instance

    @classmethod
    def _create_service(cls, kind: str) -> RAGService:
        if kind != "vector":
            st = get_rag_status()
            logger.info("Using NullRAGService (local mode): %s", st.reason[:120])
            return NullRAGService()

        class_path = settings.rag_service_class
        if class_path == "backend.services.rag.qdrant_impl.QdrantRAGService":
            logger.info("Using QdrantRAGService (vector RAG ready)")
            return QdrantRAGService()

        try:
            module_path, class_name = class_path.rsplit(".", 1)
            module = importlib.import_module(module_path)
            service_class = getattr(module, class_name)
            if not issubclass(service_class, RAGService):
                raise TypeError(f"{class_path} must inherit from RAGService")
            logger.info("Using custom RAG service: %s", class_path)
            return service_class()
        except Exception as e:
            logger.error(
                "Failed to load RAG service %s: %s. Falling back to NullRAGService.",
                class_path,
                e,
            )
            return NullRAGService()

    @classmethod
    def reset(cls) -> None:
        """重置单例（测试 / 设置变更后）"""
        cls._instance = None
        cls._instance_kind = None
        invalidate_rag_status_cache()
