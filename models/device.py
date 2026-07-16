"""
Device 模型 - 设备管理
对应前端 demo 中的 Devices 页面
"""

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin, utc_now


class Device(Base, UUIDMixin, TimestampMixin):
    """设备表：管理连接的设备及其能力"""

    __tablename__ = "devices"

    name: Mapped[str] = mapped_column(String(128), index=True)
    device_type: Mapped[str] = mapped_column(
        String(32), default="browser"
    )  # browser, shell, api, mobile
    status: Mapped[str] = mapped_column(
        String(16), default="offline"
    )  # online, offline, busy
    capabilities: Mapped[list[str]] = mapped_column(JSON, default=list)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        default=utc_now, onupdate=utc_now
    )

    # Relationships
    user: Mapped[Optional["User"]] = relationship("User", back_populates="devices")
