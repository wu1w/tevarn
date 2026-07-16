"""
Context 模型 - 上下文管理层
对应前端 demo 中的 CtxItem 和 Flow
"""

import uuid
from datetime import datetime
from enum import Enum as PyEnum
from typing import Any, Optional

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin, utc_now


class ScopeKey(str, PyEnum):
    SYSTEM = "system"
    USER = "user"
    PROJECT = "project"
    SESSION = "session"
    KNOWLEDGE = "knowledge"


class ItemKind(str, PyEnum):
    INSTRUCTION = "instruction"
    MEMORY = "memory"
    DOC = "doc"
    MESSAGE = "message"
    RAG = "rag"
    TOOL_DEF = "tool-def"


class CtxItem(Base, UUIDMixin, TimestampMixin):
    """
    上下文项 - 对应前端 CtxItem
    五个 scope：system / user / project / session / knowledge
    """

    __tablename__ = "ctx_items"

    # 所属用户（全局项可为空；session 项与 user_id 冗余保存，便于隔离）
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # 所属会话（session scope 的项绑定到具体会话，其他可为空）
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=True, index=True
    )

    scope: Mapped[ScopeKey] = mapped_column(String(20), index=True)
    kind: Mapped[ItemKind] = mapped_column(String(20), index=True)
    key: Mapped[str] = mapped_column(String(256), index=True)
    value: Mapped[str] = mapped_column(Text)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    ttl: Mapped[Optional[str]] = mapped_column(
        String(16), nullable=True
    )  # "1h" / "24h" / "session"
    origin: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        default=utc_now, onupdate=utc_now
    )

    # Relationships
    session: Mapped[Optional["Session"]] = relationship(
        "Session", back_populates="ctx_items"
    )


class ContextFlow(Base, UUIDMixin, TimestampMixin):
    """
    上下文访问流 - 记录 Agent 每轮访问了哪些上下文项
    对应前端 demo 中的 FLOWS_INIT
    """

    __tablename__ = "context_flows"

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    agent: Mapped[str] = mapped_column(String(64), index=True)
    scope: Mapped[ScopeKey] = mapped_column(String(20), index=True)
    keys: Mapped[list[str]] = mapped_column(JSON, default=list)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    # created_at 继承自 TimestampMixin

    # Relationships
    session: Mapped["Session"] = relationship("Session")
