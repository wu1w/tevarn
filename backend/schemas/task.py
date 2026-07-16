"""
Task 相关的 Pydantic Schema
"""

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class TaskCreate(BaseModel):
    """创建任务请求"""

    name: str
    description: Optional[str] = None


class TaskUpdate(BaseModel):
    """更新任务请求"""

    status: Optional[str] = None
    progress: Optional[int] = Field(None, ge=0, le=100)
    logs: Optional[list[dict[str, Any]]] = None


class TaskRead(BaseModel):
    """任务响应"""

    id: uuid.UUID
    session_id: uuid.UUID
    name: str
    description: Optional[str]
    status: str
    progress: int
    logs: list[dict[str, Any]]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
