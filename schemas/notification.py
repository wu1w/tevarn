"""
Notification 相关 Pydantic Schema
"""

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class NotificationBase(BaseModel):
    type: str = Field(..., max_length=32)
    title: str = Field(..., max_length=256)
    content: str
    data: Optional[dict[str, Any]] = None
    source_id: Optional[str] = Field(None, max_length=64)


class NotificationCreate(NotificationBase):
    user_id: uuid.UUID


class NotificationRead(NotificationBase):
    id: uuid.UUID
    user_id: uuid.UUID
    is_read: bool
    read_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}


class NotificationList(BaseModel):
    total: int
    unread: int
    items: list[NotificationRead]
