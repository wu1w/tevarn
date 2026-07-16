"""
Device 相关 Pydantic Schema
"""

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class DeviceBase(BaseModel):
    name: str = Field(..., max_length=128)
    device_type: str = Field(default="browser", max_length=32)
    status: str = Field(default="offline", max_length=16)
    capabilities: list[str] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)


class DeviceCreate(DeviceBase):
    pass


class DeviceUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=128)
    status: Optional[str] = Field(None, max_length=16)
    capabilities: Optional[list[str]] = None
    config: Optional[dict[str, Any]] = None


class DeviceRead(DeviceBase):
    id: uuid.UUID
    last_seen_at: datetime
    created_at: datetime

    model_config = {"from_attributes": True}
