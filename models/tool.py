"""
Tool 模型 - 工具表
Agent 可调用的工具定义，支持内置工具和用户自定义工具
"""

import uuid
from typing import Any, Optional

from sqlalchemy import Boolean, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin


class Tool(Base, UUIDMixin, TimestampMixin):
    """工具表：存储 Agent 可调用的工具定义"""

    __tablename__ = "tools"

    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(
        String(32)
    )  # browser, command, file_read, file_write, http, python
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # v3.0: 新增 tool_schema、risk_level、requires_confirmation、allowed_paths
    tool_schema: Mapped[dict[str, Any]] = mapped_column(
        "schema", JSON, default=lambda: {"type": "object", "properties": {}}
    )
    risk_level: Mapped[str] = mapped_column(
        String(16), default="medium"
    )  # safe/low/medium/high/dangerous
    requires_confirmation: Mapped[bool] = mapped_column(Boolean, default=False)
    allowed_paths: Mapped[list[str] | None] = mapped_column(JSON, nullable=True, default=None)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_builtin: Mapped[bool] = mapped_column(
        Boolean, default=False
    )  # 内置工具不可删除
