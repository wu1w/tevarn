"""
知识库索引流水线：文本切块 → Embedding → 写入 Qdrant + Chunk 表
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from typing import Any

from backend.core.config import settings
from backend.repositories.knowledge_repo import AsyncChunkRepository, AsyncDocumentRepository
from backend.services.embedding.factory import EmbeddingServiceFactory

logger = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 120


def split_text(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[str]:
    """按段落优先、再按长度滑动窗口切块。"""
    text = (text or "").strip()
    if not text:
        return []
    # 统一换行
    text = text.replace("\r\n", "\n")
    paragraphs = re.split(r"\n{2,}", text)
    chunks: list[str] = []
    buf = ""
    for para in paragraphs:
        p = para.strip()
        if not p:
            continue
        if len(buf) + len(p) + 1 <= chunk_size:
            buf = f"{buf}\n\n{p}".strip() if buf else p
            continue
        if buf:
            chunks.append(buf)
        if len(p) <= chunk_size:
            buf = p
        else:
            # 硬切
            start = 0
            while start < len(p):
                end = min(len(p), start + chunk_size)
                chunks.append(p[start:end])
                start = max(end - overlap, start + 1)
            buf = ""
    if buf:
        chunks.append(buf)
    # 二次合并过短块
    merged: list[str] = []
    for c in chunks:
        if merged and len(merged[-1]) < chunk_size // 3:
            merged[-1] = f"{merged[-1]}\n\n{c}"
        else:
            merged.append(c)
    return merged


async def ensure_qdrant_collection(vector_size: int, collection: str | None = None) -> dict[str, Any]:
    """确保 collection 存在（不存在则创建，同时建 BM25 全文索引支持混合检索）。"""
    import aiohttp

    url = settings.qdrant_url.rstrip("/")
    col = collection or settings.qdrant_collection
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        async with session.get(f"{url}/collections/{col}") as resp:
            if resp.status == 200:
                return {"ok": True, "existed": True, "collection": col}
        # 创建：向量索引 + BM25 全文索引（Qdrant 1.8+）
        payload = {
            "vectors": {
                "size": vector_size,
                "distance": "Cosine",
            },
            "payload_schema": {
                "text": {
                    "type": "text",
                    "tokenizer": "word",
                    "min_token_len": 2,
                    "max_token_len": 20,
                    "lowercase": True,
                },
            },
        }
        async with session.put(
            f"{url}/collections/{col}", json=payload
        ) as resp:
            text = await resp.text()
            if resp.status not in (200, 201):
                return {
                    "ok": False,
                    "message": f"创建 collection 失败 HTTP {resp.status}: {text[:300]}",
                }
            return {"ok": True, "existed": False, "collection": col}


async def upsert_qdrant_points(points: list[dict[str, Any]]) -> dict[str, Any]:
    import aiohttp

    if not points:
        return {"ok": True, "count": 0}
    url = settings.qdrant_url.rstrip("/")
    collection = settings.qdrant_collection
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as session:
        async with session.put(
            f"{url}/collections/{collection}/points",
            json={"points": points},
        ) as resp:
            text = await resp.text()
            if resp.status not in (200, 201):
                return {
                    "ok": False,
                    "message": f"Qdrant upsert 失败 HTTP {resp.status}: {text[:400]}",
                }
            return {"ok": True, "count": len(points)}


def _point_id(doc_id: str, index: int) -> str:
    # Qdrant 接受 uuid 或 unsigned int；用稳定 uuid5
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{doc_id}:{index}"))


async def index_document_text(
    *,
    document_id: uuid.UUID,
    title: str,
    text: str,
    user_id: uuid.UUID | None = None,
    source: str = "",
) -> dict[str, Any]:
    """
    对文档正文建索引：切块、embedding、写 DB chunks + Qdrant。
    """
    doc_repo = AsyncDocumentRepository()
    chunk_repo = AsyncChunkRepository()

    await doc_repo.update_status(document_id, "processing")
    pieces = split_text(text)
    if not pieces:
        await doc_repo.update_status(document_id, "error")
        return {"ok": False, "message": "文档内容为空，无法索引", "chunks": 0}

    try:
        embedder = EmbeddingServiceFactory.get_service()
        # 智能批量大小：根据维度动态选择
        from backend.services.embedding.dimension import DimensionManager

        # 先用默认 batch 做第一轮，获取维度后再调整
        vectors: list[list[float]] = []
        first_batch = True
        batch = DimensionManager.DEFAULT_BATCH_SIZE

        for i in range(0, len(pieces), batch):
            part = pieces[i : i + batch]
            vecs = await embedder.embed(part)
            if len(vecs) != len(part):
                raise RuntimeError("Embedding 返回数量与文本块不一致")
            vectors.extend(vecs)

            # 第一轮后获取维度，调整 batch size
            if first_batch and vectors:
                dim = len(vectors[0])
                smart_batch = DimensionManager.get_batch_size(dim)
                if smart_batch != batch:
                    logger.info(f"Smart batch: dim={dim} → batch={smart_batch} (was {batch})")
                    batch = smart_batch
                first_batch = False

                # 更新维度元数据
                await DimensionManager.update_on_embed_success(dim)

        dim = len(vectors[0]) if vectors else 0
        if dim <= 0:
            raise RuntimeError("Embedding 维度无效")

        col = await ensure_qdrant_collection(dim)
        if not col.get("ok"):
            await doc_repo.update_status(document_id, "error")
            return {"ok": False, "message": col.get("message") or "Qdrant 不可用", "chunks": 0}

        # 清理旧 chunks（简单：不删旧向量，新 point id 覆盖同 uuid5）
        points = []
        created = 0
        for idx, (content, vec) in enumerate(zip(pieces, vectors)):
            pid = _point_id(str(document_id), idx)
            chunk = await chunk_repo.create(
                {
                    "document_id": document_id,
                    "user_id": user_id,
                    "content": content,
                    "index": idx,
                    "vector_id": pid,
                    "meta": {"title": title, "source": source},
                }
            )
            created += 1
            points.append(
                {
                    "id": pid,
                    "vector": vec,
                    "payload": {
                        "text": content,
                        "document_id": str(document_id),
                        "chunk_id": str(chunk.id),
                        "user_id": str(user_id) if user_id else None,
                        "title": title,
                        "source": source,
                        "index": idx,
                    },
                }
            )

        up = await upsert_qdrant_points(points)
        if not up.get("ok"):
            await doc_repo.update_status(document_id, "error")
            return {
                "ok": False,
                "message": up.get("message") or "写入向量库失败",
                "chunks": created,
            }

        # 更新 chunks_count
        await doc_repo.update(
            document_id,
            {"status": "indexed", "chunks_count": created},
        )
        return {
            "ok": True,
            "message": f"已索引 {created} 个分块",
            "chunks": created,
            "collection": settings.qdrant_collection,
            "vector_dim": dim,
        }
    except Exception as e:
        logger.exception("index_document_text failed: %s", e)
        try:
            await doc_repo.update_status(document_id, "error")
        except Exception:
            pass
        return {"ok": False, "message": f"索引失败: {e}", "chunks": 0}
