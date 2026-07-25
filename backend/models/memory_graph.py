"""Memory Graph 模型（Phase 1 Memory Graph MVP）

项目知识 / 决策 / 偏好 / 经验 的图式长期记忆：
- MemoryNode：类型化记忆节点（kind: knowledge|decision|preference|experience）
- MemoryEdge：节点间关系（related_to|part_of|supports|contradicts|derived_from）

与 Entity（提及统计型实体记忆）的区别：这里是**结构化图**，
节点承载完整内容，边承载关系语义，供 agent 显式 remember/recall。
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy import ForeignKey, Integer, String, Text, JSON, Float
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin


class MemoryNode(Base, UUIDMixin, TimestampMixin):
    """记忆节点"""

    __tablename__ = "memory_nodes"

    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # knowledge | decision | preference | experience
    kind: Mapped[str] = mapped_column(String(20), index=True)
    title: Mapped[str] = mapped_column(String(200), index=True)
    content: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)

    # 来源：manual | agent | session
    source: Mapped[str] = mapped_column(String(20), default="agent")
    source_session_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)

    # 召回排序：置信度 + 命中统计
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    hit_count: Mapped[int] = mapped_column(Integer, default=0)


class MemoryEdge(Base, UUIDMixin, TimestampMixin):
    """记忆关系边"""

    __tablename__ = "memory_edges"

    from_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("memory_nodes.id", ondelete="CASCADE"), index=True
    )
    to_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("memory_nodes.id", ondelete="CASCADE"), index=True
    )
    # related_to | part_of | supports | contradicts | derived_from
    relation: Mapped[str] = mapped_column(String(20), default="related_to")
    note: Mapped[str] = mapped_column(String(300), default="")
