"""
Skill 模型 - 技能表
"""

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Boolean, String, Text
from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin, utc_now


class Skill(Base, UUIDMixin, TimestampMixin):
    """技能表：存储 Agent 可用的技能定义"""

    __tablename__ = "skills"

    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    schema: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict
    )  # JSON Schema for function calling
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    handler: Mapped[str] = mapped_column(String(32), default="http")  # http | python
    handler_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
