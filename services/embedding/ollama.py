"""
Ollama Embedding 服务实现
对接 Ollama /api/embed API
"""

import logging

import aiohttp

from backend.core.config import settings

from .interface import EmbeddingService

logger = logging.getLogger(__name__)


class OllamaEmbeddingService(EmbeddingService):
    """Ollama Embedding 服务"""

    def __init__(self, config=None):
        if config is None:
            config = settings
        self.base_url = getattr(config, "embedding_base_url", "http://localhost:11434").rstrip("/")
        self.model = getattr(config, "embedding_model", "nomic-embed-text")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """调用 Ollama /api/embed（批量）"""
        url = f"{self.base_url}/api/embed"
        payload = {
            "model": self.model,
            "input": texts,
        }
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=60)
            ) as session:
                async with session.post(url, json=payload) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
                    # Ollama /api/embed 返回 {"embeddings": [[...], [...]]}
                    embeddings = data.get("embeddings", [])
                    if embeddings:
                        return embeddings
                    # 如果返回单个 embedding（旧版格式）
                    single = data.get("embedding", [])
                    if single and len(texts) == 1:
                        return [single]
                    # 既没有 embeddings 也没有可用的 embedding 字段：
                    # 绝不能静默返回伪造的零向量（会导致 RAG 检索得到无意义结果且难以排查），
                    # 必须显式报错让调用方感知到 Embedding 服务异常。
                    raise RuntimeError(
                        f"Ollama embedding response missing 'embeddings'/'embedding' field: {data!r}"
                    )
        except Exception as e:
            logger.error(f"Ollama embed error: {e}")
            raise

    async def embed_query(self, query: str) -> list[float]:
        """编码单个查询文本"""
        results = await self.embed([query])
        return results[0] if results else []


class NoOpEmbeddingService(EmbeddingService):
    """当 Ollama 不可用时使用的兜底 embedding 服务：只返回空列表，让 RAG 安静跳过"""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[] for _ in texts]

    async def embed_query(self, query: str) -> list[float]:
        return []
