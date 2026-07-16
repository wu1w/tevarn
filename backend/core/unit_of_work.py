"""
Unit of Work（工作单元）

封装一个事务级数据库会话和该会话上的所有 Repository 实例，
确保跨 Repository 操作的原子性，并减少重复构造 AsyncXxxRepository(db) 的样板代码。

使用示例：
    async with UnitOfWork() as uow:
        session = await uow.sessions.get_by_id(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        messages = await uow.messages.get_history_by_session(session_id)
        # 退出上下文时自动 commit（无异常）或 rollback（有异常）
"""

from backend.database import get_db_context
from backend.repositories.agent_profile_repo import AsyncAgentProfileRepository
from backend.repositories.context_repo import AsyncContextFlowRepository, AsyncCtxItemRepository
from backend.repositories.cron_repo import AsyncCronJobRepository
from backend.repositories.device_repo import AsyncDeviceRepository
from backend.repositories.knowledge_repo import AsyncChunkRepository, AsyncDocumentRepository
from backend.repositories.message_repo import AsyncMessageRepository
from backend.repositories.notification_repo import AsyncNotificationRepository
from backend.repositories.session_repo import AsyncSessionRepository
from backend.repositories.setting_repo import AsyncSettingRepository
from backend.repositories.skill_repo import AsyncSkillRepository
from backend.repositories.task_repo import AsyncTaskRepository
from backend.repositories.tool_repo import AsyncToolRepository
from backend.repositories.user_repo import AsyncUserRepository
from backend.repositories.wiki_repo import AsyncWikiEntityRepository, AsyncWikiRelationRepository
from backend.repositories.workflow_repo import AsyncWorkflowRepository


class UnitOfWork:
    """提供事务级 session 和全量 Repository 访问的工作单元"""

    async def __aenter__(self) -> "UnitOfWork":
        self._context = get_db_context()
        self.session = await self._context.__aenter__()

        # 所有 Repository 共享同一个 session
        self.agent_profiles = AsyncAgentProfileRepository(self.session)
        self.chunks = AsyncChunkRepository(self.session)
        self.context_flows = AsyncContextFlowRepository(self.session)
        self.cron_jobs = AsyncCronJobRepository(self.session)
        self.ctx_items = AsyncCtxItemRepository(self.session)
        self.devices = AsyncDeviceRepository(self.session)
        self.documents = AsyncDocumentRepository(self.session)
        self.messages = AsyncMessageRepository(self.session)
        self.notifications = AsyncNotificationRepository(self.session)
        self.sessions = AsyncSessionRepository(self.session)
        self.settings = AsyncSettingRepository(self.session)
        self.skills = AsyncSkillRepository(self.session)
        self.tasks = AsyncTaskRepository(self.session)
        self.tools = AsyncToolRepository(self.session)
        self.users = AsyncUserRepository(self.session)
        self.wiki_entities = AsyncWikiEntityRepository(self.session)
        self.wiki_relations = AsyncWikiRelationRepository(self.session)
        self.workflows = AsyncWorkflowRepository(self.session)

        return self

    async def __aexit__(self, exc_type, exc, tb):
        """委托给 get_db_context()：无异常时 commit，有异常时 rollback"""
        return await self._context.__aexit__(exc_type, exc, tb)
