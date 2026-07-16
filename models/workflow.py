"""
Workflow 模型 - 工作流引擎
对应前端 demo 中的 Workflows 和 Workflow Control 页面
"""

import uuid
from typing import Any

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin


class Workflow(Base, UUIDMixin, TimestampMixin):
    """工作流表：存储 DAG 定义"""

    __tablename__ = "workflows"

    name: Mapped[str] = mapped_column(String(128), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    dag: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict
    )  # { nodes: [...], edges: [...] }
    status: Mapped[str] = mapped_column(
        String(16), default="draft"
    )  # draft, active, paused
    trigger: Mapped[str] = mapped_column(
        String(32), default="manual"
    )  # manual, webhook, cron
    variables: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
