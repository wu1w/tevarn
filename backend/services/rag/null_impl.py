"""Null RAG：无向量栈时的安全实现，永不抛连接错误。"""

from __future__ import annotations

from typing import Any

from backend.services.rag.capability import get_rag_status
from backend.services.rag.interface import Document, RAGService
from backend.services.reranker.interface import RerankedResult


class NullRAGService(RAGService):
    """Claude Code 默认路径：不连 Qdrant / Embedding。"""

    async def embed(self, query: str) -> list[float]:
        return []

    async def search(
        self,
        collection: str,
        vector: list[float],
        limit: int = 20,
        user_id: str | None = None,
    ) -> list[Document]:
        return []

    async def rerank(
        self,
        query: str,
        documents: list[str],
        top_n: int = 5,
    ) -> list[RerankedResult]:
        return []

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
        st = get_rag_status()
        hints = "；".join(st.hints) if st.hints else ""
        return (
            "[知识库检索不可用 — 当前为本地模式]\n"
            f"原因：{st.reason}\n"
            f"{('建议：' + hints) if hints else ''}\n"
            "Agent 应改用 memory.md / Wiki / 工作区文件工具，勿假装已检索到文档。"
        ).strip()
