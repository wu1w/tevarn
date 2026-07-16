"""
Session Repository 接口与实现
处理会话的 CRUD 和行级锁并发控制
"""

import uuid
from abc import abstractmethod
from typing import Any

from sqlalchemy import select

from backend.models.session import Session
from backend.schemas.session import SessionRead

from .base import AsyncBaseRepository, BaseRepository


class SessionRepository(BaseRepository):
    """Session 仓库接口"""

    @abstractmethod
    async def get_with_lock(self, session_id: uuid.UUID) -> Any | None:
        """
        获取 Session 并加行级锁 (SELECT ... FOR UPDATE)
        用于 Agent Loop 中保证并发安全
        """
        raise NotImplementedError

    @abstractmethod
    async def update_status(
        self, session_id: uuid.UUID, status: str
    ) -> Any | None:
        """更新会话状态 (idle / thinking / tool_executing)"""
        raise NotImplementedError

    @abstractmethod
    async def get_config(self, session_id: uuid.UUID) -> dict[str, Any]:
        """获取会话的四维度配置"""
        raise NotImplementedError

    @abstractmethod
    async def update_config(
        self, session_id: uuid.UUID, config: dict[str, Any]
    ) -> Any | None:
        """更新会话配置"""
        raise NotImplementedError

    @abstractmethod
    async def list_by_user(self, user_id: str) -> list[Any]:
        """列出用户的所有会话"""
        raise NotImplementedError


class AsyncSessionRepository(AsyncBaseRepository, SessionRepository):
    """基于 SQLAlchemy AsyncSession 的 Session 仓库实现"""


    async def get_by_id(self, id: Any) -> SessionRead | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(Session).where(Session.id == id))
            obj = result.scalar_one_or_none()
            return SessionRead.model_validate(obj) if obj is not None else None
        except Exception:
            await session.rollback()
            raise
        finally:
            await self._close_session(session)

    async def create(self, data: dict[str, Any]) -> SessionRead:
        session = await self._get_session()
        try:
            obj = Session(**data)
            session.add(obj)
            await self._maybe_commit(session)
            await session.refresh(obj)
            return SessionRead.model_validate(obj)
        except Exception:
            await session.rollback()
            raise
        finally:
            await self._close_session(session)

    async def update(self, id: Any, data: dict[str, Any]) -> SessionRead | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(Session).where(Session.id == id))
            obj = result.scalar_one_or_none()
            if not obj:
                return None
            for key, value in data.items():
                setattr(obj, key, value)
            await self._maybe_commit(session)
            await session.refresh(obj)
            return SessionRead.model_validate(obj)
        except Exception:
            await session.rollback()
            raise
        finally:
            await self._close_session(session)

    async def delete(self, id: Any) -> bool:
        session = await self._get_session()
        try:
            result = await session.execute(select(Session).where(Session.id == id))
            obj = result.scalar_one_or_none()
            if not obj:
                return False
            await session.delete(obj)
            await self._maybe_commit(session)
            return True
        except Exception:
            await session.rollback()
            raise
        finally:
            await self._close_session(session)

    async def get_by_id_for_user(self, id: Any, user_id: Any) -> SessionRead | None:
        session = await self._get_session()
        try:
            uid = uuid.UUID(user_id) if isinstance(user_id, str) else user_id
            result = await session.execute(
                select(Session).where(Session.id == id, Session.user_id == uid)
            )
            obj = result.scalar_one_or_none()
            return SessionRead.model_validate(obj) if obj is not None else None
        except Exception:
            await session.rollback()
            raise
        finally:
            await self._close_session(session)

    async def get_with_lock(self, session_id: uuid.UUID) -> SessionRead | None:
        # SQLite 不支持 FOR UPDATE，退化为普通查询
        return await self.get_by_id(session_id)

    async def update_status(self, session_id: uuid.UUID, status: str) -> SessionRead | None:
        return await self.update(session_id, {"status": status})

    async def get_config(self, session_id: uuid.UUID) -> dict[str, Any]:
        obj = await self.get_by_id(session_id)
        if obj is None:
            return {}
        return obj.config or {}

    async def update_config(self, session_id: uuid.UUID, config: dict[str, Any]) -> SessionRead | None:
        return await self.update(session_id, {"config": config})

    async def list_by_user(self, user_id: uuid.UUID | str) -> list[SessionRead]:
        session = await self._get_session()
        try:
            uid = uuid.UUID(user_id) if isinstance(user_id, str) else user_id
            # 返回该用户的全部会话（含空白会话），避免新建会话未发消息前就从列表消失
            result = await session.execute(
                select(Session)
                .where(Session.user_id == uid)
                .order_by(Session.created_at.desc())
            )
            return [SessionRead.model_validate(obj) for obj in result.scalars().all()]
        except Exception:
            await session.rollback()
            raise
        finally:
            await self._close_session(session)
