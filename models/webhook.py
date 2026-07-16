"""
Webhook 模型
Webhook 接收/管理 + 投递日志
"""

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import ForeignKey, String, Integer, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin, utc_now


class Webhook(Base, UUIDMixin, TimestampMixin):
    """Webhook 配置表"""

    __tablename__ = "webhooks"

    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(128), index=True)
    url: Mapped[str] = mapped_column(String(512))
    secret: Mapped[str] = mapped_column(String(256), default="")
    events: Mapped[Any] = mapped_column(JSON, default=list)  # 订阅事件列表
    workflow_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("workflows.id", ondelete="SET NULL"), nullable=True, index=True
    )
    enabled: Mapped[bool] = mapped_column(default=True)
    headers: Mapped[Any] = mapped_column(JSON, default=dict)  # 自定义请求头
    retry_on_failure: Mapped[bool] = mapped_column(default=True)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    # 运行时状态
    last_triggered_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    last_status: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    last_response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    trigger_count: Mapped[int] = mapped_column(Integer, default=0)


class WebhookDeliveryLog(Base, UUIDMixin):
    """Webhook 投递日志表"""

    __tablename__ = "webhook_delivery_logs"

    webhook_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("webhooks.id", ondelete="CASCADE"), index=True
    )
    event: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))  # success / failed / pending
    request_url: Mapped[str] = mapped_column(String(512))
    request_body: Mapped[Any] = mapped_column(JSON, nullable=True)
    response_status: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    response_body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
