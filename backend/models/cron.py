"""
Cron 模型 - 定时任务
对应前端 demo 中的 Cron 页面
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin, utc_now


class CronJob(Base, UUIDMixin, TimestampMixin):
    """定时任务表"""

    __tablename__ = "cron_jobs"

    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(128), index=True)
    schedule: Mapped[str] = mapped_column(
        String(64)
    )  # cron expression, e.g. "0 9 * * *"
    workflow_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("workflows.id", ondelete="SET NULL"), nullable=True, index=True
    )
    enabled: Mapped[bool] = mapped_column(default=True)
    last_run_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    next_run_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    last_status: Mapped[str] = mapped_column(
        String(16), default="pending"
    )  # pending, success, failed
    last_error: Mapped[Optional[str]] = mapped_column(nullable=True)
