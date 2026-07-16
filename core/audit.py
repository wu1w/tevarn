"""
审计日志辅助函数
提供非阻塞的日志写入与常用 action 常量。
"""

import logging
import uuid
from typing import Any

from fastapi import Request

from backend.repositories.audit_log_repo import AsyncAuditLogRepository

logger = logging.getLogger(__name__)

# 常用审计动作常量
class AuditAction:
    REGISTER = "auth.register"
    LOGIN = "auth.login"
    LOGOUT = "auth.logout"
    PASSWORD_CHANGE = "auth.password_change"
    ACCOUNT_DELETE = "auth.account_delete"
    DATA_EXPORT = "auth.data_export"
    SESSION_CREATE = "session.create"
    SESSION_DELETE = "session.delete"
    MESSAGE_SEND = "message.send"
    TASK_CREATE = "task.create"
    TASK_UPDATE = "task.update"
    TASK_DELETE = "task.delete"
    WORKFLOW_CREATE = "workflow.create"
    WORKFLOW_UPDATE = "workflow.update"
    WORKFLOW_DELETE = "workflow.delete"
    WORKFLOW_EXECUTE = "workflow.execute"
    CRON_CREATE = "cron.create"
    CRON_UPDATE = "cron.update"
    CRON_DELETE = "cron.delete"
    TOOL_EXECUTE = "tool.execute"
    SKILL_CREATE = "skill.create"
    SKILL_UPDATE = "skill.update"
    SKILL_DELETE = "skill.delete"
    SETTINGS_UPDATE = "settings.update"
    KNOWLEDGE_DOCUMENT_CREATE = "knowledge.document.create"
    KNOWLEDGE_DOCUMENT_DELETE = "knowledge.document.delete"
    WIKI_IMPORT = "wiki.import"
    WIKI_ENTITY_CREATE = "wiki.entity.create"
    WIKI_ENTITY_UPDATE = "wiki.entity.update"
    WIKI_ENTITY_DELETE = "wiki.entity.delete"
    AGENT_PROFILE_CREATE = "agent_profile.create"
    AGENT_PROFILE_UPDATE = "agent_profile.update"
    AGENT_PROFILE_DELETE = "agent_profile.delete"
    CONTEXT_ITEM_CREATE = "context.item.create"
    CONTEXT_ITEM_UPDATE = "context.item.update"
    CONTEXT_ITEM_DELETE = "context.item.delete"


_audit_repo = AsyncAuditLogRepository()


def _get_client_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


async def log_action(
    action: str,
    request: Request | None = None,
    user_id: uuid.UUID | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    details: dict[str, Any] | None = None,
    success: bool = True,
) -> None:
    """异步写入一条审计日志；失败不阻塞主流程。"""
    try:
        await _audit_repo.create_log(
            action=action,
            user_id=user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
            ip_address=_get_client_ip(request),
            user_agent=request.headers.get("user-agent") if request else None,
            success=success,
        )
    except Exception as e:
        logger.warning(f"Failed to write audit log: {e}")
