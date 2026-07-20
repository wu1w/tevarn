"""Entity 模型 — 长期记忆

记录用户提到的实体（项目、人名、偏好等），支持跨会话召回。
"""

import uuid
from typing import Any, Optional

from sqlalchemy import ForeignKey, Integer, String, Text, JSON, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin


class Entity(Base, UUIDMixin, TimestampMixin):
    """实体记忆 — 跨会话持久化"""

    __tablename__ = "entities"

    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    name: Mapped[str] = mapped_column(String(128), index=True)
    entity_type: Mapped[str] = mapped_column(
        String(32), index=True
    )  # project, person, preference, topic, tool, custom

    # 实体属性
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # 格式: {"deadline": "2026-08-01", "priority": "high", "status": "active"}

    # 描述
    description: Mapped[str] = mapped_column(Text, default="")

    # 关联的 session（首次提到）
    source_session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        String(36), nullable=True
    )

    # 提及统计
    mention_count: Mapped[int] = mapped_column(Integer, default=1)
    first_mentioned_at: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True
    )
    last_mentioned_at: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True
    )

    # 向量 ID（用于语义搜索）
    vector_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # 状态
    status: Mapped[str] = mapped_column(
        String(16), default="active"
    )  # active, archived, merged
