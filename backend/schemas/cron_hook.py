"""Cron Hook 联动相关 Pydantic Schema"""

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class CronHookBase(BaseModel):
    """Cron Hook 基础模型"""
    name: str = Field(..., max_length=128, description="Hook 名称")
    cron_job_id: uuid.UUID = Field(..., description="关联的定时任务 ID")
    event: str = Field(..., max_length=64, description="触发事件名称")
    target_type: str = Field(..., max_length=32, description="目标类型: workflow / webhook / agent")
    target_id: uuid.UUID = Field(..., description="目标 ID")
    payload_template: dict[str, Any] = Field(default_factory=dict, description="负载模板（支持变量插值）")
    enabled: bool = Field(default=True, description="是否启用")
    condition: Optional[str] = Field(None, max_length=256, description="触发条件表达式（可选）")


class CronHookCreate(CronHookBase):
    pass


class CronHookUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=128)
    event: Optional[str] = Field(None, max_length=64)
    target_type: Optional[str] = Field(None, max_length=32)
    target_id: Optional[uuid.UUID] = None
    payload_template: Optional[dict[str, Any]] = None
    enabled: Optional[bool] = None
    condition: Optional[str] = Field(None, max_length=256)


class CronHookRead(CronHookBase):
    id: uuid.UUID
    user_id: Optional[uuid.UUID] = None
    last_triggered_at: Optional[datetime] = None
    trigger_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CronHookExecutionLogRead(BaseModel):
    """Cron Hook 执行日志"""
    id: uuid.UUID
    hook_id: uuid.UUID
    cron_job_id: uuid.UUID
    event: str
    status: str  # success / failed / skipped
    target_type: str
    target_id: uuid.UUID
    payload: Optional[dict[str, Any]] = None
    result: Optional[dict[str, Any]] = None
    error_message: Optional[str] = None
    duration_ms: int = 0
    created_at: datetime

    model_config = {"from_attributes": True}


from backend.schemas.cron import CronJobRead


class CronJobWithHooks(BaseModel):
    """定时任务及其关联的 Hook 列表"""
    cron_job: CronJobRead
    hooks: list[CronHookRead] = []
