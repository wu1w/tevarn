"""
MCP 配置模型

定义 MCP Server 在数据库中的存储结构和 API schema。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class MCPServerConfig(BaseModel):
    """MCP Server 配置（与 client 对齐）"""

    id: uuid.UUID
    name: str = Field(..., min_length=1, max_length=64)
    description: Optional[str] = None
    transport: str = Field(..., pattern=r"^(stdio|sse)$")
    command: Optional[str] = None
    args: list[str] = Field(default_factory=list)
    url: Optional[str] = None
    env: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    timeout: float = 30.0
    risk_level: str = Field(default="medium")
    allowed_paths: Optional[list[str]] = None
    # Hermes tools.include / tools.exclude：原始 MCP 工具名；支持 fnmatch
    tools_include: Optional[list[str]] = None
    tools_exclude: Optional[list[str]] = None
    # ORM 表暂无时间戳列；可选以免 ResponseValidationError
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class MCPServerCreate(BaseModel):
    """创建 MCP Server"""

    name: str = Field(..., min_length=1, max_length=64)
    description: Optional[str] = None
    transport: str = Field(..., pattern=r"^(stdio|sse)$")
    command: Optional[str] = None
    args: list[str] = Field(default_factory=list)
    url: Optional[str] = None
    env: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    timeout: float = 30.0
    risk_level: str = "medium"
    allowed_paths: Optional[list[str]] = None
    tools_include: Optional[list[str]] = None
    tools_exclude: Optional[list[str]] = None


class MCPServerUpdate(BaseModel):
    """更新 MCP Server"""

    name: Optional[str] = Field(None, min_length=1, max_length=64)
    description: Optional[str] = None
    transport: Optional[str] = Field(None, pattern=r"^(stdio|sse)$")
    command: Optional[str] = None
    args: Optional[list[str]] = None
    url: Optional[str] = None
    env: Optional[dict[str, str]] = None
    enabled: Optional[bool] = None
    timeout: Optional[float] = None
    risk_level: Optional[str] = None
    allowed_paths: Optional[list[str]] = None
    tools_include: Optional[list[str]] = None
    tools_exclude: Optional[list[str]] = None


class MCPServerToolsPolicy(BaseModel):
    """安装后工具白名单（Hermes tools.include / exclude）。

    - tools_include 非空：仅挂载匹配的原始工具名（支持 * 通配）
    - tools_include 空/null：挂载全部，再应用 tools_exclude
    - 传 tools_include=[] 表示清空白名单（恢复全量）
    """

    tools_include: Optional[list[str]] = None
    tools_exclude: Optional[list[str]] = None


class MCPRemoteToolInfo(BaseModel):
    """远端 MCP 工具清单项（含是否在白名单内）。"""

    name: str
    description: str = ""
    selected: bool = True
    registry_name: str = ""


class MCPServerToggle(BaseModel):
    """切换启用状态"""

    enabled: bool


class MCPServerStatus(BaseModel):
    """MCP Server 连接状态

    enabled = DB 开关；connected = 运行时已连接并完成 initialize。
    二者不可混用：enabled 但连不上时 connected 必须为 false。
    """

    name: str
    transport: str
    connected: bool
    tool_count: int
    error: Optional[str] = None
    enabled: Optional[bool] = None
