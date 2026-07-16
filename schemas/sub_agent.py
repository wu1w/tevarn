"""SubAgent 子代理相关 Pydantic Schema"""

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class SubAgentBase(BaseModel):
    """子代理基础模型"""
    name: str = Field(..., max_length=64, description="子代理名称")
    description: str = Field(default="", max_length=256, description="描述")
    icon: str = Field(default="🤖", max_length=8, description="图标")
    model_ref: str = Field(..., max_length=128, description="模型引用 provider_id/model_name")
    system_prompt: str = Field(default="", description="系统提示词")
    enabled_toolsets: list[str] = Field(default_factory=list, description="启用的工具集")
    max_iterations: int = Field(default=5, ge=1, le=50, description="最大工具轮次")
    temperature: float = Field(default=0.3, ge=0.0, le=2.0, description="创意度")
    enabled: bool = Field(default=True, description="是否启用")


class SubAgentCreate(SubAgentBase):
    pass


class SubAgentUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=64)
    description: Optional[str] = Field(None, max_length=256)
    icon: Optional[str] = Field(None, max_length=8)
    model_ref: Optional[str] = Field(None, max_length=128)
    system_prompt: Optional[str] = None
    enabled_toolsets: Optional[list[str]] = None
    max_iterations: Optional[int] = Field(None, ge=1, le=50)
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0)
    enabled: Optional[bool] = None


class SubAgentRead(SubAgentBase):
    id: uuid.UUID
    user_id: Optional[uuid.UUID] = None
    sort_order: int = 0
    is_builtin: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class LLMConfig(BaseModel):
    """解析后的 LLM 配置（运行时）"""
    provider: str
    model: str
    base_url: str = ""
    api_key: str = ""
    temperature: float = 0.3
    max_tokens: int = 4096
    degraded: bool = False
    original_ref: Optional[str] = None


class ModelInventoryItem(BaseModel):
    """模型池 Inventory 条目"""
    ref: str
    provider_id: str
    provider_name: str
    provider_icon: str = "🤖"
    model_name: str
    status: str = "available"  # active / default / fallback / available
    connected: bool = True


class ModelInventoryResponse(BaseModel):
    """模型池 Inventory 响应"""
    inventory: list[ModelInventoryItem] = []
