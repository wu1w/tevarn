"""
本地 Reranker 服务实现
支持任何遵循 OpenAI 兼容格式的本地 Reranker API
如 text-embeddings-inference (TEI) 的 rerank 端点
"""

import logging

import aiohttp

from backend.core.config import settings

from .interface import RerankedResult, RerankerService

logger = logging.getLogger(__name__)


class LocalRerankerService(RerankerService):
    """本地 Reranker 服务"""

    def __init__(self, config=None):
        if config is None:
            config = settings
        self.base_url = getattr(config, "reranker_base_url", "http://localhost:8001").rstrip("/")
        self.model = getattr(config, "reranker_model", "bge-reranker-base")
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
        """调用本地 Reranker API"""
        # 尝试 TEI 格式 /rerank
        url = f"{self.base_url}/rerank"
        payload = {
            "query": query,
            "texts": documents,
            "truncate": False,
        }
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            ) as session:
                is_tei_ok = False
                results: list[RerankedResult] = []
                async with session.post(url, json=payload, headers=self._get_headers()) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for item in data:
                            idx = item.get("index", 0)
                            results.append(
                                RerankedResult(
                                    text=documents[idx],
                                    score=item.get("score", 0.0),
                                    original_index=idx,
                                )
                            )
                        is_tei_ok = True
                # 第一个响应体已完整读取并退出 async with（连接已释放），
                # 再发起第二个请求，避免在未释放的响应上下文中嵌套请求
                if is_tei_ok:
                    results.sort(key=lambda x: x.score, reverse=True)
                    return results[:top_n]
                # 如果 /rerank 失败，尝试 /v1/rerank (Cohere 兼容格式)
                return await self._try_cohere_format(session, query, documents, top_n)
        except Exception as e:
            logger.error(f"Local rerank error: {e}")
            # 端点不可用或网络异常时，使用 Embedding 余弦相似度兜底
            return await self._embedding_similarity_rerank(query, documents, top_n)

    async def _try_cohere_format(
        self,
        session: aiohttp.ClientSession,
        query: str,
        documents: list[str],
        top_n: int,
    ) -> list[RerankedResult]:
        """尝试 Cohere 兼容格式 /v1/rerank"""
        url = f"{self.base_url}/v1/rerank"
        payload = {
            "model": self.model,
            "query": query,
            "documents": documents,
            "top_n": top_n,
        }
        try:
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
            logger.error(f"Cohere-compatible rerank fallback error: {e}")
            return await self._embedding_similarity_rerank(query, documents, top_n)

    async def _embedding_similarity_rerank(
        self,
        query: str,
        documents: list[str],
        top_n: int,
    ) -> list[RerankedResult]:
        """当专用 rerank 端点不可用时，使用 Embedding 余弦相似度作为软 rerank。"""
        try:
            from backend.services.embedding.factory import EmbeddingServiceFactory

            embedding_service = EmbeddingServiceFactory.get_service()
            all_texts = [query] + documents
            embeddings = await embedding_service.embed(all_texts)
            if len(embeddings) != len(all_texts):
                raise RuntimeError("Embedding count mismatch")
            query_vec = embeddings[0]

            def _cosine(a: list[float], b: list[float]) -> float:
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
            logger.error(f"Embedding similarity rerank fallback error: {e}")
            return [
                RerankedResult(text=doc, score=1.0 - i * 0.01, original_index=i)
                for i, doc in enumerate(documents[:top_n])
            ]
