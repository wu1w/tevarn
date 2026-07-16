"""
Message 相关的 Pydantic Schema
"""

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class MessageCreate(BaseModel):
    """创建消息请求"""

    role: str
    content: str
    tool_calls: Optional[list[dict[str, Any]]] = None
    token_count: Optional[int] = None


class MessageRead(BaseModel):
    """消息响应"""

    id: uuid.UUID
    session_id: uuid.UUID
    role: str
    content: Optional[str]
    tool_calls: Optional[list[dict[str, Any]]]
    token_count: Optional[int]
    created_at: datetime

    model_config = {"from_attributes": True}
