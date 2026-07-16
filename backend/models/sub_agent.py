"""
SubAgent 模型
子代理配置表 — 从模型池选模型，独立系统提示词 + 工具集
"""

import uuid
from typing import Any, Optional

from sqlalchemy import ForeignKey, String, Integer, Float, Text, Boolean, JSON
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin


class SubAgent(Base, UUIDMixin, TimestampMixin):
    """子代理配置表"""

    __tablename__ = "sub_agents"

    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # 基本信息
    name: Mapped[str] = mapped_column(String(64), index=True)
    description: Mapped[str] = mapped_column(String(256), default="")
    icon: Mapped[str] = mapped_column(String(8), default="🤖")

    # 模型引用 — 从模型池选，存 ref 格式 "provider_id/model_name"
    model_ref: Mapped[str] = mapped_column(String(128))

    # 角色定义
    system_prompt: Mapped[str] = mapped_column(Text, default="")

    # 工具配置
    enabled_toolsets: Mapped[Any] = mapped_column(JSON, default=list)  # ["file", "terminal", "git", ...]

    # 执行参数
    max_iterations: Mapped[int] = mapped_column(Integer, default=5)
    temperature: Mapped[float] = mapped_column(Float, default=0.3)

    # 状态
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    # 模板标记
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
