"""
Setting 模型 - 运行时配置
对应前端 demo 中的 Settings 各子页面
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import String, Text, TypeDecorator
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, utc_now


class FlexibleJSON(TypeDecorator):
    """兼容历史脏数据的 JSON 列。

    历史库里 value 可能是：
    - 合法 JSON（"foo" / 1 / true / {}）
    - 未加引号的裸字符串（openrouter）
    - 空串 / 数字原生类型
    严格 JSON 列在 list_all 时会整表 500。
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect) -> Any:
        if value is None:
            return None
        if isinstance(value, (dict, list, int, float, bool)):
            return json.dumps(value, ensure_ascii=False)
        if isinstance(value, (bytes, bytearray)):
            value = value.decode("utf-8", "replace")
        s = str(value)
        # 已是合法 JSON 文本则原样写入，避免双重编码
        try:
            json.loads(s)
            return s
        except Exception:
            return json.dumps(s, ensure_ascii=False)

    def process_result_value(self, value: Any, dialect) -> Any:
        if value is None:
            return None
        if isinstance(value, (dict, list, int, float, bool)):
            return value
        if isinstance(value, (bytes, bytearray)):
            value = value.decode("utf-8", "replace")
        s = str(value)
        if s == "":
            return ""
        try:
            return json.loads(s)
        except Exception:
            # 历史裸字符串 / 加密密文等
            return s


class Setting(Base, TimestampMixin):
    """配置表：支持运行时修改的系统设置"""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[Any] = mapped_column(FlexibleJSON)
    category: Mapped[str] = mapped_column(
        String(32), index=True
    )  # llm, rag, embedding, reranker, qdrant, general
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        default=utc_now, onupdate=utc_now
    )
