"""Goal 模型 - O-KR 目标树（AIOS「我往哪儿走」）

Objective / KeyResult 两层结构：
- Objective: kind=objective, parent_id=None，进度 = 子 KR 均值
- KeyResult: kind=key_result, parent_id=objective.id，自报 progress
"""

import uuid
from typing import Optional

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin


class Goal(Base, UUIDMixin, TimestampMixin):
    """目标树节点（O 或 KR）"""

    __tablename__ = "goals"

    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(200), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(16), default="objective", index=True)  # objective / key_result
    parent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("goals.id", ondelete="CASCADE"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="active")  # active / achieved / dropped
    progress: Mapped[float] = mapped_column(default=0.0)  # KR 自报 0-100；O 由子 KR 均值计算
    owner_identity_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # kernel identity id
    due_date: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)  # ISO 日期字符串

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "title": self.title,
            "description": self.description,
            "kind": self.kind,
            "parent_id": str(self.parent_id) if self.parent_id else None,
            "status": self.status,
            "progress": self.progress,
            "owner_identity_id": self.owner_identity_id,
            "due_date": self.due_date,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
