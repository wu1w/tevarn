"""
Wiki Graph 模型 - 知识图谱
对应前端 demo 中的 Wiki Graph 页面
"""

import uuid
from typing import Any

from sqlalchemy import ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin


class WikiEntity(Base, UUIDMixin, TimestampMixin):
    """知识图谱实体"""

    __tablename__ = "wiki_entities"

    name: Mapped[str] = mapped_column(String(128), index=True, unique=True)
    entity_type: Mapped[str] = mapped_column(
        String(32), index=True
    )  # concept, person, project, tech
    description: Mapped[str] = mapped_column(Text, default="")
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class WikiRelation(Base, UUIDMixin, TimestampMixin):
    """知识图谱关系"""

    __tablename__ = "wiki_relations"

    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("wiki_entities.id", ondelete="CASCADE"), index=True
    )
    target_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("wiki_entities.id", ondelete="CASCADE"), index=True
    )
    relation_type: Mapped[str] = mapped_column(
        String(32), index=True
    )  # uses, depends_on, related_to, part_of
    weight: Mapped[float] = mapped_column(default=1.0)
    evidence: Mapped[str] = mapped_column(Text, default="")
