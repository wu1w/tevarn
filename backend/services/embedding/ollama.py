"""
Ollama Embedding：优先 /api/embed，失败则走统一多端点探测（兼容误配 base 到 llama.cpp 的情况）。
"""

from __future__ import annotations

import logging

import aiohttp

from backend.core.config import settings
from backend.services.endpoint_probe import embed_with_fallback, normalize_base_url

from .interface import EmbeddingService

logger = logging.getLogger(__name__)


class OllamaEmbeddingService(EmbeddingService):
    """Ollama Embedding（自适应回退）"""

    def __init__(self, config=None):
        if config is None:
            config = settings
        raw = getattr(config, "embedding_base_url", "http://localhost:11434") or "http://localhost:11434"
        self.base_url = normalize_base_url(raw) or raw.rstrip("/")
        self.model = getattr(config, "embedding_model", "nomic-embed-text") or ""
        self._cached_url: str | None = None
        self._cached_kind: str | None = None

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
                vecs, url, kind = await embed_with_fallback(
                    session,
                    self.base_url,
                    self.model,
                    texts,
                    {"Content-Type": "application/json"},
                    cached_url=self._cached_url,
                    cached_kind=self._cached_kind,
                )
                self._cached_url, self._cached_kind = url, kind
                return vecs
        except Exception as e:
            logger.error("Ollama embed error: %s", e)
            raise

    async def embed_query(self, query: str) -> list[float]:
        results = await self.embed([query])
        return results[0] if results else []


class NoOpEmbeddingService(EmbeddingService):
    """当 Embedding 不可用时使用的兜底：返回空列表，让 RAG 安静跳过"""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[] for _ in texts]

    async def embed_query(self, query: str) -> list[float]:
        return []
