"""
Agent Profile 相关 Pydantic Schema
"""

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class AgentProfileBase(BaseModel):
    name: str = Field(..., max_length=64)
    identity: str = "You are a helpful assistant."
    sys_prompt: str = ""
    agent_md: str = ""
    skills: list[str] = Field(default_factory=list)
    is_default: bool = False
    config: dict[str, Any] = Field(default_factory=dict)


class AgentProfileCreate(AgentProfileBase):
    pass


class AgentProfileUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=64)
    identity: Optional[str] = None
    sys_prompt: Optional[str] = None
    agent_md: Optional[str] = None
    skills: Optional[list[str]] = None
    is_default: Optional[bool] = None
    config: Optional[dict[str, Any]] = None


class AgentProfileRead(AgentProfileBase):
    id: uuid.UUID
    user_id: Optional[uuid.UUID] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
