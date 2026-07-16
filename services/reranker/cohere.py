"""
Cohere Reranker 服务实现
对接 Cohere /v1/rerank API
"""

import logging

import aiohttp

from backend.core.config import settings

from .interface import RerankedResult, RerankerService

logger = logging.getLogger(__name__)


class CohereRerankerService(RerankerService):
    """Cohere Reranker 服务"""

    def __init__(self, config=None):
        if config is None:
            config = settings
        self.base_url = getattr(config, "reranker_base_url", "https://api.cohere.com").rstrip("/")
        self.model = getattr(config, "reranker_model", "rerank-english-v2.0")
        self.api_key = getattr(config, "reranker_api_key", None)

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
        """调用 Cohere /v1/rerank"""
        url = f"{self.base_url}/v1/rerank"
        payload = {
            "model": self.model,
            "query": query,
            "documents": documents,
            "top_n": top_n,
        }
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            ) as session:
                async with session.post(url, json=payload, headers=self._get_headers()) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
                    results = []
                    for item in data.get("results", []):
                        idx = item.get("index", 0)
                        results.append(
                            RerankedResult(
                                text=documents[idx],
                                score=item.get("relevance_score", 0.0),
                                original_index=idx,
                            )
                        )
                    return results
        except Exception as e:
            logger.error(f"Cohere rerank error: {e}")
            # 失败时返回按原始顺序的默认结果
            return [
                RerankedResult(text=doc, score=1.0 - i * 0.01, original_index=i)
                for i, doc in enumerate(documents[:top_n])
            ]
