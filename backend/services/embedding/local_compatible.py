"""
通用 Embedding 服务：自适应多主流端点
OpenAI /v1/embeddings、Ollama /api/embed、TEI /embed 等。
"""

from __future__ import annotations

import logging

import aiohttp

from backend.core.config import settings
from backend.services.endpoint_probe import embed_with_fallback, normalize_base_url

from .interface import EmbeddingService

logger = logging.getLogger(__name__)


class LocalEmbeddingService(EmbeddingService):
    """自适应多端点 Embedding（OpenAI 兼容 / Ollama / TEI …）"""

    def __init__(self, config=None):
        if config is None:
            config = settings
        raw = getattr(config, "embedding_base_url", "http://localhost:8000") or "http://localhost:8000"
        self.base_url = normalize_base_url(raw) or raw.rstrip("/")
        self.model = getattr(config, "embedding_model", "bge-large-zh-v1.5") or ""
        self.api_key = getattr(config, "embedding_api_key", None)
        self._cached_url: str | None = None
        self._cached_kind: str | None = None

    def _get_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if not self.base_url:
            raise RuntimeError("embedding_base_url 未配置")
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
                vecs, url, kind = await embed_with_fallback(
                    session,
                    self.base_url,
                    self.model,
                    texts,
                    self._get_headers(),
                    cached_url=self._cached_url,
                    cached_kind=self._cached_kind,
                )
                self._cached_url, self._cached_kind = url, kind
                return vecs
        except Exception as e:
            logger.error("Local embedding error: %s", e)
            raise

    async def embed_query(self, query: str) -> list[float]:
        results = await self.embed([query])
        return results[0] if results else []
