"""
Agent Profile 模型 - Agent 多角色配置
对应前端 demo 中的 Agent Profiles 页面
"""

import uuid
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin


class AgentProfile(Base, UUIDMixin, TimestampMixin):
    """Agent 配置表：支持多角色切换"""

    __tablename__ = "agent_profiles"

    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(64), index=True, unique=True)
    identity: Mapped[str] = mapped_column(Text, default="You are a helpful assistant.")
    sys_prompt: Mapped[str] = mapped_column(Text, default="")
    agent_md: Mapped[str] = mapped_column(Text, default="")
    skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_default: Mapped[bool] = mapped_column(default=False)
    config: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict
    )  # 扩展配置字段
