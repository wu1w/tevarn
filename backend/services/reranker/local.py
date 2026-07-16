"""
本地 / 兼容 Reranker：自适应 TEI / Cohere / Jina / SiliconFlow / score 等端点。
"""

from __future__ import annotations

import logging

import aiohttp

from backend.core.config import settings
from backend.services.endpoint_probe import normalize_base_url, rerank_with_fallback

from .interface import RerankedResult, RerankerService

logger = logging.getLogger(__name__)


class LocalRerankerService(RerankerService):
    """自适应多端点 Reranker"""

    def __init__(self, config=None):
        if config is None:
            config = settings
        raw = getattr(config, "reranker_base_url", "http://localhost:8001") or "http://localhost:8001"
        self.base_url = normalize_base_url(raw) or raw.rstrip("/")
        self.model = getattr(config, "reranker_model", "bge-reranker-base") or ""
        self.api_key = getattr(config, "reranker_api_key", None)
        self._cached_url: str | None = None
        self._cached_kind: str | None = None

    def _get_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def rerank(
        self,
        query: str,
        documents: list[str],
        top_n: int = 5,
    ) -> list[RerankedResult]:
        if not documents:
            return []
        if not self.base_url:
            return await self._embedding_similarity_rerank(query, documents, top_n)
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
                ranked, url, kind = await rerank_with_fallback(
                    session,
                    self.base_url,
                    self.model,
                    query,
                    documents,
                    top_n,
                    self._get_headers(),
                    cached_url=self._cached_url,
                    cached_kind=self._cached_kind,
                )
                self._cached_url, self._cached_kind = url, kind
                results = [
                    RerankedResult(text=documents[idx], score=score, original_index=idx)
                    for idx, score in ranked
                    if 0 <= idx < len(documents)
                ]
                results.sort(key=lambda x: x.score, reverse=True)
                return results[:top_n]
        except Exception as e:
            logger.error("Local rerank error: %s", e)
            return await self._embedding_similarity_rerank(query, documents, top_n)

    async def _embedding_similarity_rerank(
        self,
        query: str,
        documents: list[str],
        top_n: int,
    ) -> list[RerankedResult]:
        """专用 rerank 不可用时，用 Embedding 余弦相似度软精排。"""
        try:
            from backend.services.embedding.factory import EmbeddingServiceFactory

            embedding_service = EmbeddingServiceFactory.get_service()
            all_texts = [query] + documents
            embeddings = await embedding_service.embed(all_texts)
            if len(embeddings) != len(all_texts):
                raise RuntimeError("Embedding count mismatch")
            query_vec = embeddings[0]

            def _cosine(a: list[float], b: list[float]) -> float:
                if not a or not b:
                    return 0.0
                n = min(len(a), len(b))
                a, b = a[:n], b[:n]
                dot = sum(x * y for x, y in zip(a, b))
                norm_a = sum(x * x for x in a) ** 0.5
                norm_b = sum(x * x for x in b) ** 0.5
                if norm_a == 0 or norm_b == 0:
                    return 0.0
                return dot / (norm_a * norm_b)

            scored = [
                RerankedResult(
                    text=doc,
                    score=_cosine(query_vec, embeddings[i + 1]),
                    original_index=i,
                )
                for i, doc in enumerate(documents)
            ]
            scored.sort(key=lambda x: x.score, reverse=True)
            return scored[:top_n]
        except Exception as e:
            logger.error("Embedding similarity rerank fallback error: %s", e)
            return [
                RerankedResult(text=doc, score=1.0 - i * 0.01, original_index=i)
                for i, doc in enumerate(documents[:top_n])
            ]
