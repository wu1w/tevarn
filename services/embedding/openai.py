"""
OpenAI Embedding 服务实现
对接 OpenAI /v1/embeddings API
"""

import logging

import aiohttp

from backend.core.config import settings

from .interface import EmbeddingService

logger = logging.getLogger(__name__)


class OpenAIEmbeddingService(EmbeddingService):
    """OpenAI Embedding 服务"""

    def __init__(self, config=None):
        if config is None:
            config = settings
        self.base_url = getattr(config, "embedding_base_url", "https://api.openai.com").rstrip("/")
        self.model = getattr(config, "embedding_model", "text-embedding-3-small")
        self.api_key = getattr(config, "embedding_api_key", None)

    def _get_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """调用 OpenAI /v1/embeddings"""
        url = f"{self.base_url}/v1/embeddings"
        payload = {
            "model": self.model,
            "input": texts,
        }
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=60)
            ) as session:
                async with session.post(url, json=payload, headers=self._get_headers()) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
                    embeddings = data.get("data", [])
                    # 按 index 排序
                    embeddings.sort(key=lambda x: x.get("index", 0))
                    return [item["embedding"] for item in embeddings]
        except Exception as e:
            logger.error(f"OpenAI embed error: {e}")
            raise

    async def embed_query(self, query: str) -> list[float]:
        """编码单个查询文本"""
        results = await self.embed([query])
        return results[0] if results else []
