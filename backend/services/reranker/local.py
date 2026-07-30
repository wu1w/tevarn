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
            logger.warning("Local rerank error: %s", e)
            # 部分模型的 llama.cpp 原生 /v1/rerank 因 prompt 模板不兼容返回坏分数。
            # 回退到 /v1/chat/completions + logprobs yes/no softmax。
            qwen = await self._qwen3_chat_rerank(query, documents, top_n)
            if qwen:
                return qwen
            return await self._embedding_similarity_rerank(query, documents, top_n)

    async def _qwen3_chat_rerank(
        self,
        query: str,
        documents: list[str],
        top_n: int,
    ) -> list[RerankedResult] | None:
        """Qwen3-Reranker 专用精排：chat/completions + logprobs yes/no softmax。

        仅对 qwen 系 reranker 生效；非 qwen 或服务不支持时返回 None 让上层降级。
        对齐 developer 的 rerank_qwen3.py：system 判相关性、enable_thinking=False、
        max_tokens=1 + top_logprobs=50，取 yes/no token 概率做 softmax 归一化。
        """
        if "qwen" not in (self.model or "").lower():
            return None
        import math

        base = self.base_url.rstrip("/")
        chat_url = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
        system_prompt = (
            "Judge whether the Document is relevant to the Query. "
            "Answer with 'yes' or 'no'."
        )
        max_doc_chars = 1500

        async def _score_one(session: aiohttp.ClientSession, doc: str) -> float:
            body = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Query: {query}\nDocument: {doc}"},
                ],
                "max_tokens": 1,
                "logprobs": True,
                "top_logprobs": 50,
                "temperature": 0.0,
                "chat_template_kwargs": {"enable_thinking": False},
            }
            async with session.post(chat_url, json=body, headers=self._get_headers()) as resp:
                if resp.status >= 400:
                    raise RuntimeError(f"chat rerank HTTP {resp.status}")
                data = await resp.json()
            choices = data.get("choices") or []
            if not choices:
                return 0.0
            lp_content = (choices[0].get("logprobs") or {}).get("content") or []
            if not lp_content:
                return 0.0
            yes_lp = no_lp = None
            for t in lp_content[0].get("top_logprobs") or []:
                raw = t.get("bytes") or []
                try:
                    s = bytes(raw).decode("utf-8", errors="replace").strip().lower()
                except Exception:
                    s = str(t.get("token", "")).strip().lower()
                if s == "yes":
                    yes_lp = t.get("logprob")
                elif s == "no":
                    no_lp = t.get("logprob")
            if yes_lp is None:
                return 0.0
            if no_lp is None:
                return 1.0
            p_y, p_n = math.exp(yes_lp), math.exp(no_lp)
            return p_y / (p_y + p_n)

        try:
            import asyncio

            truncated = [d[:max_doc_chars] for d in documents]
            # 复用单 session + 信号量限并发：llama-server 对突发多路新连接会断连
            sem = asyncio.Semaphore(2)

            async def _guarded(session: aiohttp.ClientSession, doc: str) -> float:
                async with sem:
                    return await _score_one(session, doc)

            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=60)
            ) as session:
                scores = await asyncio.gather(*[_guarded(session, d) for d in truncated])
            results = [
                RerankedResult(text=documents[i], score=float(scores[i]), original_index=i)
                for i in range(len(documents))
            ]
            results.sort(key=lambda x: x.score, reverse=True)
            logger.info("Qwen3 chat rerank ok: %d docs scored via %s", len(results), chat_url)
            return results[:top_n]
        except Exception as e:
            logger.warning("Qwen3 chat rerank failed, fallback to embedding: %s", e)
            return None

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
                dot = sum(x * y for x, y in zip(a, b, strict=False))
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
            logger.warning("Embedding similarity rerank fallback error: %s", e)
            return [
                RerankedResult(text=doc, score=1.0 - i * 0.01, original_index=i)
                for i, doc in enumerate(documents[:top_n])
            ]
