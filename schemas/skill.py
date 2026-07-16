"""
Skill 相关的 Pydantic Schema
"""

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class SkillRead(BaseModel):
    """技能响应"""

    id: uuid.UUID
    name: str
    description: Optional[str]
    skill_schema: dict[str, Any] = Field(alias="schema")
    enabled: bool
    is_builtin: bool = False
    handler: str = "http"
    handler_config: dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    model_config = {"from_attributes": True, "populate_by_name": True}


class SkillCreate(BaseModel):
    """创建自定义 Skill"""

    name: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_]+$")
    description: str = ""
    skill_schema: dict[str, Any] = Field(default_factory=dict, alias="schema")
    enabled: bool = True
    handler: str = Field("http", pattern=r"^(http|python)$")
    handler_config: dict[str, Any] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


class SkillUpdate(BaseModel):
    """更新自定义 Skill"""

    description: Optional[str] = None
    skill_schema: Optional[dict[str, Any]] = Field(default=None, alias="schema")
    enabled: Optional[bool] = None
    handler: Optional[str] = None
    handler_config: Optional[dict[str, Any]] = None

    model_config = {"populate_by_name": True}


class SkillToggle(BaseModel):
    """切换技能启用状态"""

    enabled: bool


class CommunitySkillImport(BaseModel):
    """从社区索引导入 Skill"""

    url: Optional[str] = None
    selected: list[str] = Field(default_factory=list)
