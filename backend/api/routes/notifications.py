"""
通知路由
消息通知的查询、标记已读
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException

from backend.repositories import NotificationRepository
from backend.schemas import NotificationList, NotificationRead

from ..dependencies import get_current_user, get_notification_repo

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.get("", response_model=NotificationList)
async def list_notifications(
    unread_only: bool = False,
    limit: int = 50,
    offset: int = 0,
    current_user = Depends(get_current_user),
    repo: NotificationRepository = Depends(get_notification_repo),
):
    """获取当前用户的通知列表"""
    items = await repo.list_by_user(
        current_user.id, unread_only=unread_only, limit=limit, offset=offset
    )
    total = await repo.count_by_user(current_user.id, unread_only=unread_only)
    unread = await repo.get_unread_count(current_user.id)

    return NotificationList(
        total=total,
        unread=unread,
        items=[NotificationRead.model_validate(i) for i in items],
    )


@router.post("/{notification_id}/read")
async def mark_read(
    notification_id: uuid.UUID,
    current_user = Depends(get_current_user),
    repo: NotificationRepository = Depends(get_notification_repo),
):
    """标记单条通知为已读（仅限本人通知，防止越权操作他人通知）"""
    result = await repo.mark_as_read(notification_id, user_id=current_user.id)
    if result is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"ok": True}


@router.post("/read-all")
async def mark_all_read(
    current_user = Depends(get_current_user),
    repo: NotificationRepository = Depends(get_notification_repo),
):
    """标记所有通知为已读"""
    count = await repo.mark_all_as_read(current_user.id)
    return {"ok": True, "count": count}
