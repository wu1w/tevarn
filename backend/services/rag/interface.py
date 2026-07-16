"""
RAG 服务抽象接口
定义 Embedding、向量检索、Reranker 的标准契约
用户需接入 Qdrant + Embedding + Reranker 的具体实现
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

# 复用 reranker 模块中的定义，避免两处重复定义导致的类型不一致问题
from backend.services.reranker.interface import RerankedResult

__all__ = ["Document", "RerankedResult", "RAGService"]


@dataclass
class Document:
    """检索到的文档"""

    id: str
    text: str
    score: float
    payload: dict[str, Any]


class RAGService(ABC):
    """
    RAG 服务抽象基类

    实现类需要覆盖 search_knowledge_base 方法，提供完整的 RAG 检索链路。
    embed/search/rerank 可作为可选方法保留（用于需要单独调用的场景）。
    """

    async def embed(self, query: str) -> list[float]:
        """将查询文本编码为向量（可选实现）"""
        raise NotImplementedError

    async def search(
        self,
        collection: str,
        vector: list[float],
        limit: int = 20,
        user_id: str | None = None,
    ) -> list[Document]:
        """Qdrant 向量检索（粗排）（可选实现）"""
        raise NotImplementedError

    async def rerank(
        self,
        query: str,
        documents: list[str],
        top_n: int = 5,
    ) -> list[RerankedResult]:
        """Reranker 精排（可选实现）"""
        raise NotImplementedError

    @abstractmethod
    async def search_knowledge_base(
        self,
        query: str,
        top_k: int = 5,
        collection: str | None = None,
        collections: list[str] | None = None,
        user_id: str | None = None,
        search_mode: str | None = None,
        **kwargs: Any,
    ) -> str:
        """
        完整的 RAG 检索链路：Embedding → 混合检索 → RRF融合 → Reranker精排 → 上下文组装

        Args:
            query: 查询文本
            top_k: 最终返回的文档数
            collection: 单个 Qdrant collection 名
            collections: 多个 collection 名（逻辑名或实际名）
            user_id: 用户 ID（用于过滤）
            search_mode: hybrid | vector | keyword
            **kwargs: 额外参数

        Returns:
            格式化后的上下文字符串（供 LLM 使用）
        """
        raise NotImplementedError

    def _format_context(self, results: list[RerankedResult]) -> str:
        """将精排结果格式化为 LLM 可用的上下文字符串"""
        if not results:
            return ""
        lines = ["# 检索到的相关知识", ""]
        for i, r in enumerate(results, 1):
            lines.append(f"## 文档 {i} (相关度: {r.score:.3f})")
            lines.append(r.text)
            lines.append("")
        return "\n".join(lines)
