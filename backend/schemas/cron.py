"""
Cron 相关 Pydantic Schema
"""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class CronJobBase(BaseModel):
    name: str = Field(..., max_length=128)
    schedule: str = Field(..., max_length=64)
    workflow_id: Optional[uuid.UUID] = None
    enabled: bool = True


class CronJobCreate(CronJobBase):
    pass


class CronJobUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=128)
    schedule: Optional[str] = Field(None, max_length=64)
    workflow_id: Optional[uuid.UUID] = None
    enabled: Optional[bool] = None


class CronJobRead(CronJobBase):
    id: uuid.UUID
    user_id: Optional[uuid.UUID] = None
    last_run_at: Optional[datetime]
    next_run_at: Optional[datetime]
    last_status: str
    last_error: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
