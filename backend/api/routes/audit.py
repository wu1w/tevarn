"""
审计日志路由
仅管理员可查看系统审计日志。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from backend.repositories import AuditLogRepository
from backend.schemas.audit_log import AuditLogList
from backend.schemas.user import UserRead

from ..dependencies import get_audit_log_repo, require_admin

router = APIRouter(prefix="/audit/logs", tags=["Audit"])


@router.get("", response_model=AuditLogList)
async def list_audit_logs(
    current_user: Annotated[UserRead, Depends(require_admin)],
    repo: Annotated[AuditLogRepository, Depends(get_audit_log_repo)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """分页获取审计日志（仅管理员）"""
    items, total = await repo.list_logs(limit=limit, offset=offset)
    return AuditLogList(items=items, total=total)
