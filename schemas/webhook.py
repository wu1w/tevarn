"""Webhook 相关 Pydantic Schema"""

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class WebhookBase(BaseModel):
    """Webhook 基础模型"""
    name: str = Field(..., max_length=128, description="Webhook 名称")
    url: str = Field(..., max_length=512, description="接收端 URL")
    secret: str = Field(default="", max_length=256, description="签名密钥")
    events: list[str] = Field(default_factory=list, description="订阅事件列表")
    workflow_id: Optional[uuid.UUID] = Field(None, description="触发时执行的工作流 ID")
    enabled: bool = Field(default=True, description="是否启用")
    headers: dict[str, str] = Field(default_factory=dict, description="自定义请求头")
    retry_on_failure: bool = Field(default=True, description="失败是否重试")
    max_retries: int = Field(default=3, ge=0, le=10, description="最大重试次数")


class WebhookCreate(WebhookBase):
    pass


class WebhookUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=128)
    url: Optional[str] = Field(None, max_length=512)
    secret: Optional[str] = Field(None, max_length=256)
    events: Optional[list[str]] = None
    workflow_id: Optional[uuid.UUID] = None
    enabled: Optional[bool] = None
    headers: Optional[dict[str, str]] = None
    retry_on_failure: Optional[bool] = None
    max_retries: Optional[int] = Field(None, ge=0, le=10)


class WebhookRead(WebhookBase):
    id: uuid.UUID
    user_id: Optional[uuid.UUID] = None
    last_triggered_at: Optional[datetime] = None
    last_status: Optional[str] = None
    last_response: Optional[str] = None
    trigger_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class WebhookTriggerRequest(BaseModel):
    """触发 Webhook 的请求体（外部调用）"""
    event: str = Field(..., max_length=64, description="事件名称")
    payload: dict[str, Any] = Field(default_factory=dict, description="事件负载")
    headers: dict[str, str] = Field(default_factory=dict, description="额外请求头")


class WebhookTriggerResult(BaseModel):
    """Webhook 触发结果"""
    accepted: bool = True
    message: str = ""
    triggered_workflow: bool = False
    execution_id: Optional[str] = None


class WebhookDeliveryLogRead(BaseModel):
    """Webhook 投递日志"""
    id: uuid.UUID
    webhook_id: uuid.UUID
    event: str
    status: str  # success / failed / pending
    request_url: str
    request_body: Optional[dict[str, Any]] = None
    response_status: Optional[int] = None
    response_body: Optional[str] = None
    error_message: Optional[str] = None
    duration_ms: int = 0
    created_at: datetime

    model_config = {"from_attributes": True}
