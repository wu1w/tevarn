"""
OpenAI Embedding：同样走多端点自适应（云端通常命中 /v1/embeddings）。
"""

from __future__ import annotations

import logging

import aiohttp

from backend.core.config import settings
from backend.services.endpoint_probe import embed_with_fallback, normalize_base_url

from .interface import EmbeddingService

logger = logging.getLogger(__name__)


class OpenAIEmbeddingService(EmbeddingService):
    """OpenAI / 兼容云服务 Embedding"""

    def __init__(self, config=None):
        if config is None:
            config = settings
        raw = getattr(config, "embedding_base_url", "https://api.openai.com") or "https://api.openai.com"
        self.base_url = normalize_base_url(raw) or raw.rstrip("/")
        self.model = getattr(config, "embedding_model", "text-embedding-3-small") or ""
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
            logger.error("OpenAI embed error: %s", e)
            raise

    async def embed_query(self, query: str) -> list[float]:
        results = await self.embed([query])
        return results[0] if results else []
