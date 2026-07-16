"""
Setting 模型 - 运行时配置
对应前端 demo 中的 Settings 各子页面
"""

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, utc_now


class Setting(Base, TimestampMixin):
    """配置表：支持运行时修改的系统设置"""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    category: Mapped[str] = mapped_column(
        String(32), index=True
    )  # llm, rag, embedding, reranker, qdrant, general
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        default=utc_now, onupdate=utc_now
    )
