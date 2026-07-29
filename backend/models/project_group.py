"""项目组（企业 IM 群）：一批派活的聚合视图，工单真源仍是 inbox。"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy import ForeignKey, Integer, String, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin


class ProjectGroup(Base, UUIDMixin, TimestampMixin):
    """项目组群：侧栏展示；点进去看各成员工单进度。"""

    __tablename__ = "project_groups"

    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)  # open|done
    created_by: Mapped[str] = mapped_column(String(32), default="system")  # user|ceo|system
    # members: [{identity_id, name}]
    members: Mapped[Any] = mapped_column(JSON, default=list)
    # tasks: [{inbox_item_id, identity_id, identity_name}]
    tasks: Mapped[Any] = mapped_column(JSON, default=list)
    summary: Mapped[str] = mapped_column(Text, default="")
    meta: Mapped[Any] = mapped_column(JSON, default=dict)
