import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, String, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


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
