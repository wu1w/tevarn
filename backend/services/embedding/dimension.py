"""
Embedding 维度元数据管理
跟踪当前 Embedding 模型的维度信息，检测维度不匹配
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from backend.core.config import settings

logger = logging.getLogger(__name__)

# 元数据文件路径（与 takton.db 同目录）
_METADATA_DIR = Path(".")
_METADATA_FILE = _METADATA_DIR / "embedding_metadata.json"


@dataclass
class EmbeddingMetadata:
    """Embedding 维度元数据"""

    provider: str = ""
    model: str = ""
    vector_size: int = 0
    last_check: float = 0.0  # Unix timestamp
    last_embed_success: float = 0.0
    total_embed_calls: int = 0
    total_errors: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EmbeddingMetadata:
        return cls(
            provider=data.get("provider", ""),
            model=data.get("model", ""),
            vector_size=data.get("vector_size", 0),
            last_check=data.get("last_check", 0.0),
            last_embed_success=data.get("last_embed_success", 0.0),
            total_embed_calls=data.get("total_embed_calls", 0),
            total_errors=data.get("total_errors", 0),
        )


class DimensionManager:
    """
    维度兼容性管理器
    检测 Embedding 维度与 Qdrant collection 维度是否匹配
    """

    # 智能批量大小映射：维度 → batch size
    BATCH_SIZE_MAP: dict[int, int] = {
        384: 64,    # small models (MiniLM, bge-small)
        768: 32,    # medium models (bge-base, nomic-embed-text)
        1024: 24,   # larger models
        1536: 16,   # OpenAI text-embedding-3-small / ada-002
        3072: 8,    # OpenAI text-embedding-3-large
        4096: 4,    # very large models
    }

    DEFAULT_BATCH_SIZE = 16

    @classmethod
    def get_batch_size(cls, vector_size: int) -> int:
        """根据向量维度选择最优批量大小"""
        # 精确匹配
        if vector_size in cls.BATCH_SIZE_MAP:
            return cls.BATCH_SIZE_MAP[vector_size]
        # 找最近的较小维度
        for dim in sorted(cls.BATCH_SIZE_MAP.keys(), reverse=True):
            if vector_size >= dim:
                return cls.BATCH_SIZE_MAP[dim]
        # 维度比所有已知维度都小，用最大 batch
        return cls.BATCH_SIZE_MAP.get(384, cls.DEFAULT_BATCH_SIZE)

    @classmethod
    def load_metadata(cls) -> EmbeddingMetadata:
        """从磁盘加载元数据"""
        try:
            if _METADATA_FILE.exists():
                data = json.loads(_METADATA_FILE.read_text(encoding="utf-8"))
                return EmbeddingMetadata.from_dict(data)
        except Exception as e:
            logger.warning(f"Failed to load embedding metadata: {e}")
        return EmbeddingMetadata()

    @classmethod
    def save_metadata(cls, meta: EmbeddingMetadata) -> None:
        """保存元数据到磁盘"""
        try:
            _METADATA_FILE.write_text(
                json.dumps(meta.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"Failed to save embedding metadata: {e}")

    @classmethod
    async def check_qdrant_dimension(cls, collection: str | None = None) -> dict[str, Any]:
        """
        检查 Qdrant collection 的向量维度
        Returns: {"ok": bool, "collection_dim": int|None, "embedding_dim": int|None, "match": bool}
        """
        import aiohttp

        col = collection or settings.qdrant_collection
        url = settings.qdrant_url.rstrip("/")

        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            ) as session:
                async with session.get(f"{url}/collections/{col}") as resp:
                    if resp.status != 200:
                        return {
                            "ok": False,
                            "collection_dim": None,
                            "embedding_dim": None,
                            "match": False,
                            "message": f"Collection '{col}' 不存在或 Qdrant 不可达",
                        }
                    data = await resp.json()
                    col_info = data.get("result", {})
                    col_dim = col_info.get("vectors", {}).get("size")
                    if col_dim is not None:
                        col_dim = int(col_dim)
        except Exception as e:
            return {
                "ok": False,
                "collection_dim": None,
                "embedding_dim": None,
                "match": False,
                "message": f"Qdrant 连接失败: {e}",
            }

        # 获取当前 Embedding 维度
        meta = cls.load_metadata()
        emb_dim = meta.vector_size

        if emb_dim == 0:
            # 尝试 probe embedding
            try:
                from backend.services.embedding.factory import EmbeddingServiceFactory

                svc = EmbeddingServiceFactory.get_service()
                vec = await svc.embed_query("dimension probe")
                emb_dim = len(vec)
                # 更新元数据
                meta.provider = settings.embedding_provider
                meta.model = settings.embedding_model
                meta.vector_size = emb_dim
                meta.last_check = time.time()
                cls.save_metadata(meta)
            except Exception as e:
                logger.warning(f"Embedding probe failed: {e}")

        match = col_dim is not None and emb_dim > 0 and col_dim == emb_dim

        return {
            "ok": True,
            "collection_dim": col_dim,
            "embedding_dim": emb_dim,
            "match": match,
            "collection": col,
            "message": "维度匹配" if match else f"维度不匹配: collection={col_dim}, embedding={emb_dim}",
        }

    @classmethod
    async def update_on_embed_success(cls, vector_size: int) -> None:
        """Embedding 成功后更新元数据"""
        meta = cls.load_metadata()
        changed = False

        if meta.vector_size != vector_size:
            logger.info(
                f"Embedding dimension changed: {meta.vector_size} → {vector_size} "
                f"(provider={settings.embedding_provider}, model={settings.embedding_model})"
            )
            meta.vector_size = vector_size
            changed = True

        if meta.provider != settings.embedding_provider:
            if meta.provider != settings.embedding_provider or meta.model != settings.embedding_model:
                logger.info(
                    f"Embedding config changed: {meta.provider}/{meta.model} → "
                    f"{settings.embedding_provider}/{settings.embedding_model}"
                )
                meta.provider = settings.embedding_provider
                meta.model = settings.embedding_model
                changed = True
        else:
            meta.provider = settings.embedding_provider
            meta.model = settings.embedding_model
            changed = True

        meta.last_embed_success = time.time()
        meta.total_embed_calls += 1
        cls.save_metadata(meta)

        if changed:
            # 维度或模型变更时，检查与 Qdrant 是否匹配
            result = await cls.check_qdrant_dimension()
            if result.get("ok") and not result.get("match"):
                logger.warning(
                    f"⚠️ Dimension mismatch detected: {result.get('message')}"
                )

    @classmethod
    async def record_embed_error(cls) -> None:
        """记录 Embedding 错误"""
        meta = cls.load_metadata()
        meta.total_errors += 1
        cls.save_metadata(meta)
