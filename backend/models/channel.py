"""
消息通道（Channel）数据库模型

兼容 Hermes 的 platform 配置体系，支持配置各类通信软件 Bot：
Telegram、Discord、Slack、WeCom、QQ Bot、飞书、Signal 等。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Boolean, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, TimestampMixin, UUIDMixin


class Channel(Base, UUIDMixin, TimestampMixin):
    """消息通道配置表"""

    __tablename__ = "channels"

    # 通道类型标识（对应 Hermes Platform enum）
    platform: Mapped[str] = mapped_column(String(32), index=True)
    # 通道显示名称
    name: Mapped[str] = mapped_column(String(128))
    # 通道描述
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 是否启用
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # 是否已连接（运行时状态，DB 只存快照）
    connected: Mapped[bool] = mapped_column(Boolean, default=False)
    # Bot Token（加密存储）
    token: Mapped[str | None] = mapped_column(Text, nullable=True)
    # API Key（如与 token 不同）
    api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 主频道 ID（home channel）
    home_channel_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # 平台特有配置（Hermes extra 字段）
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Webhook URL（部分平台需要配置回调地址）
    webhook_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # 上次连接测试时间
    last_tested_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 上次连接测试结果
    last_test_result: Mapped[str | None] = mapped_column(Text, nullable=True)
