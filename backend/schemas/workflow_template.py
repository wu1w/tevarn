"""Workflow Template 相关 Pydantic Schema"""

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class WorkflowTemplateBase(BaseModel):
    """工作流模板基础模型"""
    name: str = Field(..., max_length=128, description="模板名称")
    description: str = Field(default="", max_length=1024, description="模板描述")
    category: str = Field(default="general", max_length=64, description="模板分类")
    icon: str = Field(default="file-text", description="图标标识")
    color: str = Field(default="#6366f1", description="主题色")
    dag: dict[str, Any] = Field(default_factory=dict, description="模板 DAG 结构")
    variables: dict[str, Any] = Field(default_factory=dict, description="模板变量定义")
    tags: list[str] = Field(default_factory=list, description="标签")
    is_builtin: bool = Field(default=False, description="是否为内置模板")
    version: str = Field(default="1.0", max_length=16, description="模板版本")


class WorkflowTemplateCreate(WorkflowTemplateBase):
    pass


class WorkflowTemplateUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=128)
    description: Optional[str] = Field(None, max_length=1024)
    category: Optional[str] = Field(None, max_length=64)
    icon: Optional[str] = None
    color: Optional[str] = None
    dag: Optional[dict[str, Any]] = None
    variables: Optional[dict[str, Any]] = None
    tags: Optional[list[str]] = None
    version: Optional[str] = Field(None, max_length=16)


class WorkflowTemplateRead(WorkflowTemplateBase):
    id: uuid.UUID
    user_id: Optional[uuid.UUID] = None
    use_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TemplateCreateWorkflowRequest(BaseModel):
    """从模板创建工作流的请求"""
    template_id: uuid.UUID = Field(..., description="模板 ID")
    name: str = Field(..., max_length=128, description="新工作流名称")
    description: str = Field(default="", description="新工作流描述")
    variables: dict[str, Any] = Field(default_factory=dict, description="模板变量覆盖")


class TemplateCreateWorkflowResult(BaseModel):
    """从模板创建工作流的结果"""
    workflow_id: uuid.UUID
    workflow_name: str
    template_name: str
    message: str


class TemplateCategory(BaseModel):
    """模板分类统计"""
    category: str
    count: int
