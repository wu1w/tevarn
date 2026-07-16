"""
WorkflowTemplate 模型
工作流模板（内置 + 用户自定义）
"""

import uuid
from typing import Any, Optional

from sqlalchemy import ForeignKey, String, Integer, Boolean, JSON
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin


class WorkflowTemplate(Base, UUIDMixin, TimestampMixin):
    """工作流模板表"""

    __tablename__ = "workflow_templates"

    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(128), index=True)
    description: Mapped[str] = mapped_column(String(1024), default="")
    category: Mapped[str] = mapped_column(String(64), default="general", index=True)
    icon: Mapped[str] = mapped_column(String(64), default="file-text")
    color: Mapped[str] = mapped_column(String(16), default="#6366f1")
    dag: Mapped[Any] = mapped_column(JSON, default=dict)
    variables: Mapped[Any] = mapped_column(JSON, default=dict)
    tags: Mapped[Any] = mapped_column(JSON, default=list)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[str] = mapped_column(String(16), default="1.0")
    use_count: Mapped[int] = mapped_column(Integer, default=0)
