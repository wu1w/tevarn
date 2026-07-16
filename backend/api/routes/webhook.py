"""
Webhook 路由
Webhook 管理 API：CRUD + 触发 + 投递日志
"""

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.models.webhook import Webhook, WebhookDeliveryLog
from backend.repositories.webhook_repo import AsyncWebhookRepository, AsyncWebhookDeliveryLogRepository
from backend.schemas.webhook import (
    WebhookCreate,
    WebhookRead,
    WebhookUpdate,
    WebhookDeliveryLogRead,
    WebhookTriggerResult,
)
from backend.schemas.user import UserRead

from ..dependencies import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])

_webhook_repo = AsyncWebhookRepository()
_delivery_log_repo = AsyncWebhookDeliveryLogRepository()


async def get_webhook_repo() -> AsyncWebhookRepository:
    return _webhook_repo


async def get_delivery_log_repo() -> AsyncWebhookDeliveryLogRepository:
    return _delivery_log_repo


@router.get("", response_model=list[WebhookRead])
async def list_webhooks(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: AsyncWebhookRepository = Depends(get_webhook_repo),
):
    """列出当前用户的 Webhook"""
    return await repo.list_by_user(current_user.id) or []


@router.post("", response_model=WebhookRead, status_code=201)
async def create_webhook(
    data: WebhookCreate,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: AsyncWebhookRepository = Depends(get_webhook_repo),
):
    """创建 Webhook"""
    obj = await repo.create({**data.model_dump(), "user_id": current_user.id})
    logger.info(f"Webhook created: {obj.id} ({obj.name})")
    return obj


@router.get("/{webhook_id}", response_model=WebhookRead)
async def get_webhook(
    webhook_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: AsyncWebhookRepository = Depends(get_webhook_repo),
):
    """获取单个 Webhook"""
    obj = await repo.get_by_id(webhook_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Webhook not found")
    if obj.user_id and obj.user_id != current_user.id and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Access denied")
    return obj


@router.put("/{webhook_id}", response_model=WebhookRead)
async def update_webhook(
    webhook_id: uuid.UUID,
    data: WebhookUpdate,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: AsyncWebhookRepository = Depends(get_webhook_repo),
):
    """更新 Webhook"""
    obj = await repo.get_by_id(webhook_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Webhook not found")
    if obj.user_id and obj.user_id != current_user.id and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Access denied")
    update_data = data.model_dump(exclude_unset=True)
    return await repo.update(webhook_id, update_data)


@router.delete("/{webhook_id}", status_code=204)
async def delete_webhook(
    webhook_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: AsyncWebhookRepository = Depends(get_webhook_repo),
):
    """删除 Webhook"""
    obj = await repo.get_by_id(webhook_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Webhook not found")
    if obj.user_id and obj.user_id != current_user.id and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Access denied")
    await repo.delete(webhook_id)
    logger.info(f"Webhook deleted: {webhook_id}")


@router.post("/{webhook_id}/test", response_model=WebhookTriggerResult)
async def test_webhook(
    webhook_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: AsyncWebhookRepository = Depends(get_webhook_repo),
    log_repo: AsyncWebhookDeliveryLogRepository = Depends(get_delivery_log_repo),
):
    """测试 Webhook 连通性"""
    obj = await repo.get_by_id(webhook_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Webhook not found")

    import time
    import httpx

    start = time.time()
    status = "pending"
    resp_status = None
    resp_body = None
    error_msg = None

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                obj.url,
                json={"event": "test", "payload": {"source": "takton-test"}},
                headers=obj.headers or {},
            )
            resp_status = resp.status_code
            resp_body = resp.text[:500]
            status = "success" if 200 <= resp.status_code < 300 else "failed"
    except Exception as e:
        status = "failed"
        error_msg = str(e)[:500]

    duration_ms = int((time.time() - start) * 1000)

    # 记录投递日志
    await log_repo.create({
        "webhook_id": webhook_id,
        "event": "test",
        "status": status,
        "request_url": obj.url,
        "request_body": {"event": "test", "payload": {"source": "takton-test"}},
        "response_status": resp_status,
        "response_body": resp_body,
        "error_message": error_msg,
        "duration_ms": duration_ms,
    })

    # 更新 webhook 状态
    await repo.update(webhook_id, {
        "last_triggered_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        "last_status": status,
        "last_response": resp_body,
        "trigger_count": (obj.trigger_count or 0) + 1,
    })

    return WebhookTriggerResult(
        accepted=status == "success",
        message=f"Test {status} in {duration_ms}ms",
        triggered_workflow=False,
    )


@router.get("/{webhook_id}/logs", response_model=list[WebhookDeliveryLogRead])
async def get_webhook_logs(
    webhook_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: AsyncWebhookRepository = Depends(get_webhook_repo),
    log_repo: AsyncWebhookDeliveryLogRepository = Depends(get_delivery_log_repo),
    limit: int = Query(default=50, ge=1, le=200),
):
    """获取 Webhook 投递日志"""
    obj = await repo.get_by_id(webhook_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Webhook not found")
    return await log_repo.list_by_webhook(webhook_id, limit) or []
