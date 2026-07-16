"""
Qdrant RAG 服务实现
完整的 Embedding -> 混合检索(BM25+Vector+RRF) -> 精排(Reranker) 链路
支持多 Collection 并行检索 + 跨源 RRF 融合
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from backend.core.config import settings
from backend.services.embedding.factory import EmbeddingServiceFactory
from backend.services.embedding.interface import EmbeddingService
from backend.services.reranker.factory import RerankerServiceFactory
from backend.services.reranker.interface import RerankedResult, RerankerService

from .interface import Document, RAGService

logger = logging.getLogger(__name__)


@dataclass
class RAGDiagnostics:
    """检索诊断信息 — 仅供前端展示，不注入 LLM 上下文"""

    query: str = ""
    total_time_ms: float = 0.0
    embed_time_ms: float = 0.0
    search_time_ms: float = 0.0
    rerank_time_ms: float = 0.0
    vector_count: int = 0       # 向量检索命中数
    bm25_count: int = 0         # BM25 检索命中数
    fused_count: int = 0        # RRF 融合后数量
    reranked_count: int = 0     # 精排后数量
    collections_searched: list[str] = field(default_factory=list)
    search_mode: str = "hybrid"  # hybrid | vector | keyword
    errors: list[str] = field(default_factory=list)


class QdrantRAGService(RAGService):
    """Qdrant RAG 服务 — 支持混合检索 + 多 Collection 路由"""

    def __init__(
        self,
        embedding_service: EmbeddingService | None = None,
        reranker_service: RerankerService | None = None,
    ):
        self.embedding_service = embedding_service or EmbeddingServiceFactory.get_service()
        self.reranker_service = reranker_service or RerankerServiceFactory.get_service()
        self.qdrant_url = settings.qdrant_url
        self._diagnostics: RAGDiagnostics | None = None

    async def embed(self, query: str) -> list[float]:
        """文本向量化（委托给 Embedding 服务）"""
        return await self.embedding_service.embed_query(query)

    # ─── 混合检索 ───

    async def hybrid_search(
        self,
        collection: str,
        query: str,
        vector: list[float],
        limit: int = 20,
        user_id: str | None = None,
    ) -> list[Document]:
        """混合检索：向量检索 + BM25 全文检索 + Qdrant 原生 RRF 融合"""
        import aiohttp

        url = f"{self.qdrant_url}/collections/{collection}/points/search"

        # Qdrant prefetch + rrf 融合
        payload: dict[str, Any] = {
            "prefetch": [
                {"query": vector, "limit": limit},                          # 向量检索
                {"query": {"text": query}, "limit": limit, "using": "text"},  # BM25
            ],
            "limit": limit,
            "with_payload": True,
            "fusion": "rrf",
        }
        if user_id:
            payload["filter"] = {
                "must": [{"key": "user_id", "match": {"value": user_id}}]
            }

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30)
        ) as session:
            async with session.post(url, json=payload) as resp:
                resp.raise_for_status()
                data = await resp.json()
                results = []
                for item in data.get("result", []):
                    payload_data = item.get("payload", {})
                    results.append(
                        Document(
                            id=str(item.get("id", "")),
                            text=payload_data.get("text", ""),
                            score=item.get("score", 0.0),
                            payload=payload_data,
                        )
                    )
                return results

    async def _vector_only_search(
        self,
        collection: str,
        vector: list[float],
        limit: int = 20,
        user_id: str | None = None,
    ) -> list[Document]:
        """纯向量检索（降级模式）"""
        import aiohttp

        url = f"{self.qdrant_url}/collections/{collection}/points/search"
        payload: dict[str, Any] = {
            "vector": vector,
            "limit": limit,
            "with_payload": True,
        }
        if user_id:
            payload["filter"] = {
                "must": [{"key": "user_id", "match": {"value": user_id}}]
            }

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30)
        ) as session:
            async with session.post(url, json=payload) as resp:
                resp.raise_for_status()
                data = await resp.json()
                results = []
                for item in data.get("result", []):
                    payload_data = item.get("payload", {})
                    results.append(
                        Document(
                            id=str(item.get("id", "")),
                            text=payload_data.get("text", ""),
                            score=item.get("score", 0.0),
                            payload=payload_data,
                        )
                    )
                return results

    async def _keyword_only_search(
        self,
        collection: str,
        query: str,
        limit: int = 20,
        user_id: str | None = None,
    ) -> list[Document]:
        """纯 BM25 关键词检索"""
        import aiohttp

        url = f"{self.qdrant_url}/collections/{collection}/points/search"
        payload: dict[str, Any] = {
            "query": {"text": query},
            "limit": limit,
            "with_payload": True,
            "using": "text",
        }
        if user_id:
            payload["filter"] = {
                "must": [{"key": "user_id", "match": {"value": user_id}}]
            }

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30)
        ) as session:
            async with session.post(url, json=payload) as resp:
                resp.raise_for_status()
                data = await resp.json()
                results = []
                for item in data.get("result", []):
                    payload_data = item.get("payload", {})
                    results.append(
                        Document(
                            id=str(item.get("id", "")),
                            text=payload_data.get("text", ""),
                            score=item.get("score", 0.0),
                            payload=payload_data,
                        )
                    )
                return results

    async def search(
        self,
        collection: str,
        vector: list[float],
        limit: int = 20,
        user_id: str | None = None,
        query_text: str | None = None,
        search_mode: str | None = None,
    ) -> list[Document]:
        """
        检索入口：根据 search_mode 选择检索策略
        - hybrid: BM25+Vector+RRF（默认，优先尝试，失败降级）
        - vector: 纯向量检索
        - keyword: 纯 BM25 关键词检索
        """
        mode = search_mode or settings.rag_search_mode

        if mode == "keyword" and query_text:
            try:
                return await self._keyword_only_search(collection, query_text, limit, user_id)
            except Exception as e:
                logger.warning(f"Keyword search failed: {e}")
                return []

        if mode == "hybrid" and query_text:
            try:
                return await self.hybrid_search(collection, query_text, vector, limit, user_id)
            except Exception as e:
                logger.warning(f"Hybrid search failed, falling back to vector-only: {e}")
                # 降级到纯向量检索

        # 纯向量检索（默认降级 / mode=vector）
        try:
            return await self._vector_only_search(collection, vector, limit, user_id)
        except Exception as e:
            logger.error(f"Vector search error: {e}")
            return []

    # ─── 多 Collection 并行检索 + RRF 融合 ───

    def _reciprocal_rank_fusion(
        self,
        result_lists: list[list[Document]],
        top_k: int = 5,
        k: int = 60,
    ) -> list[Document]:
        """Reciprocal Rank Fusion — 多源结果融合排序"""
        scores: dict[str, float] = {}
        doc_map: dict[str, Document] = {}

        for results in result_lists:
            for rank, doc in enumerate(results):
                if doc.id not in scores:
                    scores[doc.id] = 0.0
                    doc_map[doc.id] = doc
                scores[doc.id] += 1.0 / (k + rank + 1)

        ranked = sorted(scores.items(), key=lambda x: -x[1])
        return [doc_map[doc_id] for doc_id, _ in ranked[:top_k]]

    async def search_multi_collection(
        self,
        query: str,
        vector: list[float],
        collections: list[str] | None = None,
        top_k: int = 5,
        user_id: str | None = None,
        search_mode: str | None = None,
    ) -> list[Document]:
        """多 Collection 并行检索 + RRF 融合"""
        # 解析 collection 名称列表
        if collections:
            # 传入的可能是逻辑名或实际 collection 名
            col_map = getattr(settings, "qdrant_collections", {})
            target_cols = []
            for c in collections:
                target_cols.append(col_map.get(c, c))  # 逻辑名→实际名，非逻辑名直接用
        else:
            # 使用默认检索范围
            default_keys = getattr(settings, "rag_default_collections", ["knowledge"])
            col_map = getattr(settings, "qdrant_collections", {})
            target_cols = [col_map.get(k, k) for k in default_keys]

        if not target_cols:
            target_cols = [settings.qdrant_collection]

        # 并行检索
        tasks = [
            self.search(
                col, vector, limit=top_k * 3, user_id=user_id,
                query_text=query, search_mode=search_mode,
            )
            for col in target_cols
        ]
        results_list = await asyncio.gather(*tasks, return_exceptions=True)

        # 过滤异常 + 打来源标签
        valid_results: list[list[Document]] = []
        for col, result in zip(target_cols, results_list):
            if isinstance(result, Exception):
                logger.warning(f"Collection {col} search failed: {result}")
                continue
            for doc in result:
                doc.payload["_source_collection"] = col
            valid_results.append(result)

        if not valid_results:
            return []

        # 单源直接返回，多源 RRF 融合
        if len(valid_results) == 1:
            return valid_results[0][:top_k]

        return self._reciprocal_rank_fusion(valid_results, top_k=top_k)

    # ─── Reranker ───

    async def rerank(
        self,
        query: str,
        documents: list[str],
        top_n: int = 5,
    ) -> list[RerankedResult]:
        """Reranker 精排（委托给 Reranker 服务）"""
        return await self.reranker_service.rerank(query, documents, top_n)

    # ─── 完整检索链路 ───

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
        """
        完整的 RAG 检索链路
        Embedding → 混合检索(多Collection) → RRF融合 → Reranker精排 → 上下文组装
        """
        diag = RAGDiagnostics(query=query[:100], search_mode=search_mode or settings.rag_search_mode)
        t_start = time.monotonic()

        if not getattr(settings, "rag_enabled", True):
            return ""
        if not query or not query.strip():
            return ""

        logger.info(f"RAG search: query='{query[:50]}...', top_k={top_k}, user_id={user_id}")

        try:
            # 1. Embedding
            t0 = time.monotonic()
            vector = await self.embed(query)
            diag.embed_time_ms = (time.monotonic() - t0) * 1000

            if not vector:
                logger.warning("RAG embed returned empty vector")
                diag.errors.append("Embedding returned empty vector")
                self._diagnostics = diag
                return ""

            # 2. 检索（单 Collection 或多 Collection）
            t1 = time.monotonic()
            if collection:
                # 单 collection 模式
                docs = await self.search(
                    collection, vector, limit=max(top_k * 4, 8),
                    user_id=user_id, query_text=query, search_mode=search_mode,
                )
                diag.collections_searched = [collection]
            else:
                # 多 collection 模式
                docs = await self.search_multi_collection(
                    query, vector, collections=collections,
                    top_k=top_k, user_id=user_id, search_mode=search_mode,
                )
                col_map = getattr(settings, "qdrant_collections", {})
                default_keys = getattr(settings, "rag_default_collections", ["knowledge"])
                diag.collections_searched = [col_map.get(k, k) for k in default_keys]

            diag.search_time_ms = (time.monotonic() - t1) * 1000
            diag.fused_count = len(docs)

            if not docs:
                self._diagnostics = diag
                return ""

            # 3. 精排 (Reranker) — 失败则退回粗排分数
            t2 = time.monotonic()
            doc_texts = [d.text for d in docs]
            doc_payloads = [d.payload for d in docs]
            try:
                reranked = await self.rerank(query, doc_texts, top_n=top_k)
                diag.reranked_count = len(reranked)
            except Exception as e:
                logger.warning("Rerank failed, using vector scores: %s", e)
                diag.errors.append(f"Rerank failed: {e}")
                ordered = sorted(docs, key=lambda d: d.score, reverse=True)[:top_k]
                reranked = [
                    RerankedResult(text=d.text, score=float(d.score), original_index=i)
                    for i, d in enumerate(ordered)
                ]
                diag.reranked_count = len(reranked)

            diag.rerank_time_ms = (time.monotonic() - t2) * 1000
            diag.total_time_ms = (time.monotonic() - t_start) * 1000

            # 4. 上下文组装（使用 ContextAssembler）
            try:
                from backend.services.rag.context_assembler import ContextAssembler
                assembler = ContextAssembler()
                context = assembler.assemble(reranked, doc_payloads[:len(reranked)])
            except ImportError:
                # fallback：旧格式
                context = self._format_context(reranked)

            self._diagnostics = diag
            return context

        except Exception as e:
            logger.error("RAG search_knowledge_base failed: %s", e)
            diag.errors.append(str(e))
            diag.total_time_ms = (time.monotonic() - t_start) * 1000
            self._diagnostics = diag
            return ""

    def get_diagnostics(self) -> RAGDiagnostics | None:
        """获取最近一次检索的诊断信息"""
        return self._diagnostics



class QdrantService:
    """兼容 self_config 旧工具接口（get_system_status / manage_knowledge）。"""

    def __init__(self) -> None:
        self.qdrant_url = getattr(settings, "qdrant_url", "") or ""
        self._rag = QdrantRAGService()

    async def get_collections(self) -> dict[str, Any]:
        import aiohttp

        url = f"{self.qdrant_url.rstrip('/')}/collections"
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get(url) as resp:
                resp.raise_for_status()
                data = await resp.json()
        cols: list[str] = []
        for c in (data.get("result") or {}).get("collections") or []:
            if isinstance(c, dict) and c.get("name"):
                cols.append(str(c["name"]))
            elif isinstance(c, str):
                cols.append(c)
        return {"collections": cols}

    async def add_document(self, doc: Any = None, **kwargs: Any) -> Any:
        """写入知识库并索引。接受 Document 实例或 title/content kwargs。"""
        title = kwargs.get("title")
        content = kwargs.get("content") or kwargs.get("text") or ""
        if doc is not None:
            title = title or getattr(doc, "title", None) or (getattr(doc, "payload", None) or {}).get("title")
            content = content or getattr(doc, "content", None) or getattr(doc, "text", None) or ""
        title = title or "untitled"
        from backend.repositories.knowledge_repo import AsyncDocumentRepository
        from backend.services.knowledge.indexer import index_document_text

        repo = AsyncDocumentRepository()
        row = await repo.create(
            {
                "title": str(title),
                "source": "tool-upload",
                "status": "ready",
                "meta": {"content": content, "origin": "QdrantService.add_document"},
            }
        )
        await index_document_text(
            document_id=row.id,
            title=str(title),
            text=str(content or ""),
            user_id=getattr(row, "user_id", None),
            source="tool-upload",
            skip_wiki=True,
            replace_chunks=True,
        )

        class _Doc:
            pass

        out = _Doc()
        out.id = str(row.id)
        out.title = str(title)
        out.content = content
        return out

    async def list_documents(self) -> list[dict[str, Any]]:
        from backend.repositories.knowledge_repo import AsyncDocumentRepository

        repo = AsyncDocumentRepository()
        rows = await repo.list_all()
        out: list[dict[str, Any]] = []
        for r in rows or []:
            out.append(
                {
                    "id": str(getattr(r, "id", "")),
                    "title": getattr(r, "title", "") or "",
                    "status": getattr(r, "status", "") or "",
                    "source": getattr(r, "source", "") or "",
                }
            )
        return out

    async def delete_document(self, doc_id: str) -> None:
        import uuid as _uuid

        from backend.repositories.knowledge_repo import AsyncDocumentRepository

        repo = AsyncDocumentRepository()
        await repo.delete(_uuid.UUID(str(doc_id)))

    async def search(
        self,
        query: str,
        top_k: int = 5,
        collection: str | None = None,
        **kwargs: Any,
    ) -> list[Any]:
        # 走完整知识库检索（含 embed）；失败返回空列表而非抛导入级错误
        try:
            ctx = await self._rag.search_knowledge_base(query=query, top_k=top_k)
            if not ctx:
                return []
            return [{"text": ctx, "score": 1.0}]
        except Exception as e:
            logger.warning("QdrantService.search failed: %s", e)
            return []
