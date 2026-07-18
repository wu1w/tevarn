"""
FastAPI 依赖注入
提供 Repository、Service 等依赖
"""

from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from backend.core.config import settings
from backend.core.security import decode_access_token
from backend.repositories import (
    AgentProfileRepository,
    AuditLogRepository,
    ChunkRepository,
    ContextFlowRepository,
    CronJobRepository,
    CtxItemRepository,
    DeviceRepository,
    DocumentRepository,
    MessageRepository,
    NotificationRepository,
    SessionRepository,
    SettingRepository,
    SkillRepository,
    TaskRepository,
    ToolRepository,
    UserRepository,
    WikiEntityRepository,
    WikiRelationRepository,
    WorkflowRepository,
)
from backend.repositories.user_repo import AsyncUserRepository
from backend.repositories.session_repo import AsyncSessionRepository
from backend.repositories.notification_repo import AsyncNotificationRepository
from backend.repositories.message_repo import AsyncMessageRepository
from backend.repositories.task_repo import AsyncTaskRepository
from backend.repositories.context_repo import AsyncCtxItemRepository, AsyncContextFlowRepository
from backend.repositories.skill_repo import AsyncSkillRepository
from backend.repositories.device_repo import AsyncDeviceRepository
from backend.repositories.workflow_repo import AsyncWorkflowRepository
from backend.repositories.cron_repo import AsyncCronJobRepository
from backend.repositories.cron_execution_log_repo import AsyncCronExecutionLogRepository
from backend.repositories.workflow_execution_repo import AsyncWorkflowExecutionRepository
from backend.repositories.knowledge_repo import AsyncDocumentRepository, AsyncChunkRepository
from backend.repositories.wiki_repo import AsyncWikiEntityRepository, AsyncWikiRelationRepository
from backend.repositories.setting_repo import AsyncSettingRepository
from backend.repositories.agent_profile_repo import AsyncAgentProfileRepository
from backend.repositories.audit_log_repo import AsyncAuditLogRepository
from backend.repositories.tool_repo import AsyncToolRepository
from backend.repositories.webhook_repo import AsyncWebhookRepository, AsyncWebhookDeliveryLogRepository
from backend.repositories.workflow_template_repo import AsyncWorkflowTemplateRepository
from backend.repositories.cron_hook_repo import AsyncCronHookRepository, AsyncCronHookExecutionLogRepository
from backend.repositories.sub_agent_repo import AsyncSubAgentRepository
from backend.schemas import UserRead
from backend.services.llm import LLMService, LLMServiceFactory
from backend.services.rag import RAGService, RAGServiceFactory
from backend.skills import SkillRegistry


# ---- Security ----

async def verify_api_key(x_api_key: Annotated[str, Header()]) -> str:
    """验证 API Key"""
    if x_api_key != settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API Key",
        )
    return x_api_key


# ---- Authentication ----

async def get_current_user(
    authorization: str | None = Header(default=None, alias="Authorization"),
    user_repo: UserRepository = Depends(lambda: _user_repo),
) -> UserRead:
    """从 Authorization Header 提取并验证 JWT，返回当前用户。

    - 有 Bearer 且有效 → 对应用户
    - 有 Bearer 但无效/过期 → 401（禁止静默回落，避免会话 403 身份错乱）
    - 无 Bearer 且 single_user_mode → 默认 admin@takton.dev
    """
    has_bearer = bool(authorization and authorization.startswith("Bearer "))
    if has_bearer:
        token = authorization[7:]  # type: ignore[index]
        payload = decode_access_token(token)
        if payload and "sub" in payload:
            import uuid

            try:
                user_id = uuid.UUID(str(payload["sub"]))
            except (ValueError, TypeError):
                user_id = None  # type: ignore[assignment]
            if user_id is not None:
                user = await user_repo.get_by_id(user_id)
                if user and getattr(user, "is_active", True):
                    return UserRead.model_validate(user)
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="User not found or deactivated",
                    headers={"WWW-Authenticate": "Bearer"},
                )
        # token 无法解码 / 缺 sub：明确 401，让前端清 token 重登
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if settings.single_user_mode:
        # 查找或创建默认用户
        default = await user_repo.get_by_email("admin@takton.dev")
        if default:
            return UserRead.model_validate(default)
        # 数据库尚未初始化用户，创建默认用户（密码可由 TAKTON_DEFAULT_ADMIN_PASSWORD 注入）
        from backend.core.security import get_password_hash
        from sqlalchemy.exc import IntegrityError
        import os

        default_pw = (
            (settings.default_admin_password or "").strip()
            or os.environ.get("TAKTON_DEFAULT_ADMIN_PASSWORD", "").strip()
            or "admin"
        )
        try:
            user = await user_repo.create(
                {
                    "email": "admin@takton.dev",
                    "username": "admin",
                    "hashed_password": get_password_hash(default_pw),
                    "is_superuser": True,
                    "is_active": True,
                }
            )
            return UserRead.model_validate(user)
        except IntegrityError:
            # 并发创建导致唯一约束冲突，回滚后重新获取
            default = await user_repo.get_by_email("admin@takton.dev")
            if default:
                return UserRead.model_validate(default)
            raise

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization[7:]
    payload = decode_access_token(token)
    if not payload or "sub" not in payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    import uuid

    user_id = uuid.UUID(payload["sub"])
    user = await user_repo.get_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not getattr(user, "is_active", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated",
        )
    return UserRead.model_validate(user)


# ---- Authorization ----

async def require_admin(
    current_user: UserRead = Depends(get_current_user),
) -> UserRead:
    """要求当前用户为管理员（is_superuser）"""
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


def assert_session_owner(session_user_id, current_user: UserRead) -> None:
    """校验会话归属。

    single_user_mode 下本机可访问任意会话（桌面单用户产品语义，
    避免 admin@takton.dev 与其它本地账号之间的 403 错乱）。
    多用户模式仍严格按 user_id 隔离。
    """
    if session_user_id is None:
        return
    if settings.single_user_mode:
        return
    if session_user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")


# ---- Services (工厂模式) ----

async def get_llm_service() -> LLMService:
    """获取 LLM 服务"""
    return LLMServiceFactory.get_service()


async def get_rag_service() -> RAGService:
    """获取 RAG 服务"""
    return RAGServiceFactory.get_service()


# ---- Repositories (真实实现) ----

# 所有 Repository 已实现为 AsyncXxxRepository，见下方依赖注入


# ---- Dependency Providers ----

_session_repo = AsyncSessionRepository()
_message_repo = AsyncMessageRepository()
_task_repo = AsyncTaskRepository()
_skill_repo = AsyncSkillRepository()
_ctx_item_repo = AsyncCtxItemRepository()
_context_flow_repo = AsyncContextFlowRepository()
_device_repo = AsyncDeviceRepository()
_workflow_repo = AsyncWorkflowRepository()
_cron_repo = AsyncCronJobRepository()
_document_repo = AsyncDocumentRepository()
_chunk_repo = AsyncChunkRepository()
_wiki_entity_repo = AsyncWikiEntityRepository()
_wiki_relation_repo = AsyncWikiRelationRepository()
_setting_repo = AsyncSettingRepository()
_agent_profile_repo = AsyncAgentProfileRepository()
_user_repo = AsyncUserRepository()
_notification_repo = AsyncNotificationRepository()
_tool_repo = AsyncToolRepository()
_audit_log_repo = AsyncAuditLogRepository()
_webhook_repo = AsyncWebhookRepository()
_webhook_delivery_log_repo = AsyncWebhookDeliveryLogRepository()
_workflow_template_repo = AsyncWorkflowTemplateRepository()
_cron_hook_repo = AsyncCronHookRepository()
_cron_hook_execution_log_repo = AsyncCronHookExecutionLogRepository()
_sub_agent_repo = AsyncSubAgentRepository()


async def get_session_repo() -> SessionRepository:
    return _session_repo


async def get_message_repo() -> MessageRepository:
    return _message_repo


async def get_task_repo() -> TaskRepository:
    return _task_repo


async def get_skill_repo() -> SkillRepository:
    return _skill_repo


async def get_ctx_item_repo() -> CtxItemRepository:
    return _ctx_item_repo


async def get_context_flow_repo() -> ContextFlowRepository:
    return _context_flow_repo


async def get_device_repo() -> DeviceRepository:
    return _device_repo


async def get_workflow_repo() -> WorkflowRepository:
    return _workflow_repo


async def get_workflow_execution_repo() -> AsyncWorkflowExecutionRepository:
    return AsyncWorkflowExecutionRepository()


async def get_cron_repo() -> CronJobRepository:
    return _cron_repo


async def get_document_repo() -> DocumentRepository:
    return _document_repo


async def get_chunk_repo() -> ChunkRepository:
    return _chunk_repo


async def get_wiki_entity_repo() -> WikiEntityRepository:
    return _wiki_entity_repo


async def get_wiki_relation_repo() -> WikiRelationRepository:
    return _wiki_relation_repo


async def get_setting_repo() -> SettingRepository:
    return _setting_repo


async def get_agent_profile_repo() -> AgentProfileRepository:
    return _agent_profile_repo


async def get_user_repo() -> UserRepository:
    return _user_repo


async def get_notification_repo() -> NotificationRepository:
    return _notification_repo


async def get_skill_registry() -> SkillRegistry:
    return SkillRegistry


async def get_tool_repo() -> ToolRepository:
    return _tool_repo


async def get_audit_log_repo() -> AuditLogRepository:
    return _audit_log_repo


async def get_webhook_repo() -> AsyncWebhookRepository:
    return _webhook_repo


async def get_webhook_delivery_log_repo() -> AsyncWebhookDeliveryLogRepository:
    return _webhook_delivery_log_repo


async def get_workflow_template_repo() -> AsyncWorkflowTemplateRepository:
    return _workflow_template_repo


async def get_cron_hook_repo() -> AsyncCronHookRepository:
    return _cron_hook_repo


async def get_cron_hook_execution_log_repo() -> AsyncCronHookExecutionLogRepository:
    return _cron_hook_execution_log_repo


async def get_cron_execution_log_repo() -> AsyncCronExecutionLogRepository:
    return AsyncCronExecutionLogRepository()


async def get_sub_agent_repo() -> AsyncSubAgentRepository:
    return _sub_agent_repo
