"""
Workflow 相关 Pydantic Schema
"""

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class WorkflowNode(BaseModel):
    """工作流节点"""

    id: str = Field(..., description="节点唯一标识")
    type: str = Field(..., description="节点类型: input/output/llm/agent/rag/python/http/condition/loop/merge/custom")
    label: str = Field(default="", description="节点显示名称")
    position: dict[str, float] = Field(default_factory=dict, description="画布位置 {x, y}")
    config: dict[str, Any] = Field(default_factory=dict, description="节点配置参数")


class WorkflowEdge(BaseModel):
    """工作流边（连接）"""

    id: str = Field(..., description="边唯一标识")
    from_: str = Field(..., alias="from", description="源节点ID")
    to: str = Field(..., description="目标节点ID")
    fromPort: str = Field(default="output", description="源端口名")
    toPort: str = Field(default="input", description="目标端口名")
    condition: str | None = Field(default=None, description="条件表达式（条件分支时使用）")

    model_config = {"populate_by_name": True}


class WorkflowDag(BaseModel):
    """工作流 DAG 结构"""

    nodes: list[WorkflowNode] = Field(default_factory=list)
    edges: list[WorkflowEdge] = Field(default_factory=list)


class WorkflowBase(BaseModel):
    name: str = Field(..., max_length=128)
    description: str = ""
    dag: dict[str, Any] = Field(default_factory=dict)
    status: str = Field(default="draft", max_length=16)
    trigger: str = Field(default="manual", max_length=32)
    variables: dict[str, Any] = Field(default_factory=dict)
    user_id: uuid.UUID | None = None


class WorkflowCreate(WorkflowBase):
    pass


class WorkflowUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=128)
    description: Optional[str] = None
    dag: Optional[dict[str, Any]] = None
    status: Optional[str] = Field(None, max_length=16)
    trigger: Optional[str] = Field(None, max_length=32)
    variables: Optional[dict[str, Any]] = None


class WorkflowRead(WorkflowBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class WorkflowExecuteRequest(BaseModel):
    """工作流执行请求"""

    inputs: dict[str, Any] = Field(default_factory=dict, description="初始输入数据")


class WorkflowExecuteResult(BaseModel):
    """工作流执行结果"""

    success: bool = True
    outputs: dict[str, Any] = Field(default_factory=dict)
    logs: list[dict[str, Any]] = Field(default_factory=list)
    execution_time_ms: int = 0
