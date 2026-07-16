"""
Knowledge 模型 - 知识库文档管理
对应前端 demo 中的 Knowledge (RAG) 页面
"""

import uuid
from typing import Any, Optional

from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin


class Document(Base, UUIDMixin, TimestampMixin):
    """文档表：管理上传的原始文档"""

    __tablename__ = "documents"

    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(256))
    source: Mapped[str] = mapped_column(
        String(512), default=""
    )  # 文件路径或 URL
    status: Mapped[str] = mapped_column(
        String(16), default="pending"
    )  # pending, processing, indexed, error
    chunks_count: Mapped[int] = mapped_column(Integer, default=0)
    meta: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict
    )  # mime_type, size, etc.


class Chunk(Base, UUIDMixin, TimestampMixin):
    """文档分块表：存储向量化后的文本块"""

    __tablename__ = "chunks"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    content: Mapped[str] = mapped_column(Text)
    index: Mapped[int] = mapped_column(Integer, default=0)
    vector_id: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )  # 向量数据库中的 ID
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
