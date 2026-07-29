"""
Session 相关的 Pydantic Schema
"""

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class SessionConfig(BaseModel):
    """四维度心智配置"""

    # LLM 人设文案（进 system prompt 的 identity 层）
    identity: str = "You are a helpful assistant."
    sys_prompt: str = ""
    agent_md: str = ""
    skills: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    auto_optimize: bool = True
    optimize_threshold: float = Field(0.7, ge=0.0, le=1.0)
    # AIOS：从员工 Profile「联系 TA」进入时写入的编制名（AgentIdentity.name）
    # 与 identity 人设文案分离——UI 展示 / 标题用这个，prompt 用 identity。
    contact_agent: Optional[str] = None
    # human_dm | workforce | project_group — workforce 不进聊天列表
    source: Optional[str] = None
    workforce: Optional[bool] = None
    workforce_identity_id: Optional[str] = None
    project_group_id: Optional[str] = None


class SessionCreate(BaseModel):
    """创建会话请求"""

    user_id: Optional[str] = None
    config: Optional[SessionConfig] = None


class ContactSessionOpen(BaseModel):
    """一人一会话：按员工名 find-or-create"""

    name: str = Field(..., min_length=1, max_length=64)
    identity_text: Optional[str] = None


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
