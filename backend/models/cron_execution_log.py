"""
Cron 执行日志模型
每次 Cron 运行一条记录，便于排查定时任务历史。
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin, utc_now


class CronExecutionLog(Base, UUIDMixin, TimestampMixin):
    """Cron 执行日志表"""

    __tablename__ = "cron_execution_logs"

    cron_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cron_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(16), default="running"
    )  # running, success, failed
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(default=utc_now)
    finished_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    output: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(nullable=True)
