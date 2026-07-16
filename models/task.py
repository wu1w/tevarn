"""
Task 模型 - 异步任务表
"""

import uuid
from datetime import datetime
from enum import Enum as PyEnum
from typing import Any, Optional

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin, utc_now


class TaskStatus(str, PyEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Task(Base, UUIDMixin, TimestampMixin):
    """任务表：存储异步长任务的进度与日志"""

    __tablename__ = "tasks"

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[TaskStatus] = mapped_column(String(20), default=TaskStatus.PENDING)
    progress: Mapped[int] = mapped_column(Integer, default=0)  # 0-100
    logs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)

    # Relationships
    session: Mapped["Session"] = relationship("Session", back_populates="tasks")
