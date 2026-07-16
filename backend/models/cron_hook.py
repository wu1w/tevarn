"""
CronHook 模型
Cron 任务触发后的 Hook 联动（触发工作流/Webhook/Agent）
"""

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import ForeignKey, String, Integer, JSON, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin, utc_now


class CronHook(Base, UUIDMixin, TimestampMixin):
    """Cron Hook 联动配置表"""

    __tablename__ = "cron_hooks"

    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(128), index=True)
    cron_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cron_jobs.id", ondelete="CASCADE"), index=True
    )
    event: Mapped[str] = mapped_column(String(64))  # on_success / on_failure / on_run
    target_type: Mapped[str] = mapped_column(String(32))  # workflow / webhook / agent
    target_id: Mapped[uuid.UUID] = mapped_column()  # 目标资源 ID
    payload_template: Mapped[Any] = mapped_column(JSON, default=dict)  # 负载模板
    enabled: Mapped[bool] = mapped_column(default=True)
    condition: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)  # 触发条件表达式
    # 运行时状态
    last_triggered_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    trigger_count: Mapped[int] = mapped_column(Integer, default=0)


class CronHookExecutionLog(Base, UUIDMixin):
    """Cron Hook 执行日志表"""

    __tablename__ = "cron_hook_execution_logs"

    hook_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cron_hooks.id", ondelete="CASCADE"), index=True
    )
    cron_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cron_jobs.id", ondelete="CASCADE"), index=True
    )
    event: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))  # success / failed / skipped
    target_type: Mapped[str] = mapped_column(String(32))
    target_id: Mapped[uuid.UUID] = mapped_column()
    payload: Mapped[Any] = mapped_column(JSON, nullable=True)
    result: Mapped[Any] = mapped_column(JSON, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
