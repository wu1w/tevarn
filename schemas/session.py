"""
Session 相关的 Pydantic Schema
"""

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class SessionConfig(BaseModel):
    """四维度心智配置"""

    identity: str = "You are a helpful assistant."
    sys_prompt: str = ""
    agent_md: str = ""
    skills: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    auto_optimize: bool = True
    optimize_threshold: float = Field(0.7, ge=0.0, le=1.0)


class SessionCreate(BaseModel):
    """创建会话请求"""

    user_id: Optional[str] = None
    config: Optional[SessionConfig] = None


class SessionConfigUpdate(BaseModel):
    """更新会话配置请求"""

    config: SessionConfig


class SessionRead(BaseModel):
    """会话响应"""

    id: uuid.UUID
    user_id: Optional[uuid.UUID]
    status: str
    config: Optional[dict[str, Any]] = {}
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
