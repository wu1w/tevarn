"""
MCP Server 数据库模型
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Boolean, JSON, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class MCPServer(Base):
    """MCP Server 配置表"""

    __tablename__ = "mcp_servers"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    transport: Mapped[str] = mapped_column(String(16))  # stdio | sse
    command: Mapped[str | None] = mapped_column(String(512), nullable=True)
    args: Mapped[list[str]] = mapped_column(JSON, default=list)
    url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    env: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    timeout: Mapped[float] = mapped_column(Float, default=30.0)
    risk_level: Mapped[str] = mapped_column(String(16), default="medium")
    allowed_paths: Mapped[list[str] | None] = mapped_column(JSON, nullable=True, default=None)
