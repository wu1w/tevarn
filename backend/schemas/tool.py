"""
Tool 相关的 Pydantic Schema
"""

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class ToolRead(BaseModel):
    """工具响应"""

    id: uuid.UUID
    name: str
    description: str
    type: str
    config: dict[str, Any]
    tool_schema: dict[str, Any]
    risk_level: str
    requires_confirmation: bool
    allowed_paths: list[str] | None
    enabled: bool
    is_builtin: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ToolCreate(BaseModel):
    """创建工具"""

    name: str = Field(..., min_length=1, max_length=64)
    description: str
    type: str = Field(
        ...,
        pattern=r"^(browser|command|file_read|file_write|http|python|search|edit|glob|grep|sqlite_query)$"
    )
    config: dict[str, Any] = Field(default_factory=dict)
    tool_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    risk_level: str = "medium"
    requires_confirmation: bool = False
    allowed_paths: list[str] | None = None
    enabled: bool = True


class ToolUpdate(BaseModel):
    """更新工具"""

    description: Optional[str] = None
    config: Optional[dict[str, Any]] = None
    tool_schema: Optional[dict[str, Any]] = None
    risk_level: Optional[str] = None
    requires_confirmation: Optional[bool] = None
    allowed_paths: Optional[list[str]] = None
    enabled: Optional[bool] = None


class ToolToggle(BaseModel):
    """切换工具启用状态"""

    enabled: bool


class ToolExecuteRequest(BaseModel):
    """执行工具请求"""

    arguments: dict[str, Any]


class ToolExecuteResponse(BaseModel):
    """执行工具响应"""

    success: bool
    result: str
    tool_name: str
