import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from backend.core.timezone import utc_now as _utc_now


def utc_now() -> datetime:
    """ORM 字段默认值入口，统一走 backend.core.timezone（禁止裸 datetime.now）。"""
    return _utc_now()


class Base(DeclarativeBase):
    """所有 ORM 模型的基类"""

    type_annotation_map = {
        uuid.UUID: Uuid(native_uuid=False),
        datetime: DateTime(timezone=True),
    }


class TimestampMixin:
    """通用时间戳混入类"""

    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        default=utc_now, onupdate=utc_now
    )


class UUIDMixin:
    """通用 UUID 主键混入类"""

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4
    )
