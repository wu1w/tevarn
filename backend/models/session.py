"""
Session 模型 - 会话表
"""

import uuid
from datetime import datetime
from enum import Enum as PyEnum
from typing import Any, Optional

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin, utc_now


class SessionStatus(str, PyEnum):
    IDLE = "idle"
    THINKING = "thinking"
    TOOL_EXECUTING = "tool_executing"


class Session(Base, UUIDMixin, TimestampMixin):
    """会话表：存储每个聊天会话的配置与状态"""

    __tablename__ = "sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[SessionStatus] = mapped_column(
        String(20), default=SessionStatus.IDLE
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    config: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=lambda: {
            "identity": "You are a helpful assistant.",
            "sys_prompt": "",
            "agent_md": "",
            "skills": [],
        },
    )

    # Relationships
    user: Mapped[Optional["User"]] = relationship("User", back_populates="sessions")
    messages: Mapped[list["Message"]] = relationship(
        "Message", back_populates="session", cascade="all, delete-orphan"
    )
    tasks: Mapped[list["Task"]] = relationship(
        "Task", back_populates="session", cascade="all, delete-orphan"
    )
    ctx_items: Mapped[list["CtxItem"]] = relationship(
        "CtxItem", back_populates="session", cascade="all, delete-orphan"
    )
    context_flows: Mapped[list["ContextFlow"]] = relationship(
        "ContextFlow", back_populates="session", cascade="all, delete-orphan"
    )
