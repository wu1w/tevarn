"""
Session Repository 接口与实现
处理会话的 CRUD 和行级锁并发控制
"""

import asyncio
import uuid
from abc import abstractmethod
from typing import Any

from sqlalchemy import select

from backend.models.session import Session
from backend.schemas.session import SessionRead

from .base import AsyncBaseRepository, BaseRepository

# 并发修复：config 键级合并的进程内每会话锁。
# checkpoint(_agent_checkpoint) 与 goal(_goal) 等多条路径各自「读→改→整体写回」
# 会在 await 间隙互相覆盖对方的键；SQLite 无 FOR UPDATE，用进程锁串行。
_config_merge_locks: dict[str, asyncio.Lock] = {}
_CONFIG_MERGE_LOCK_MAX = 1024


def _config_merge_lock(session_id: uuid.UUID) -> asyncio.Lock:
    key = str(session_id)
    lock = _config_merge_locks.get(key)
    if lock is None:
        if len(_config_merge_locks) >= _CONFIG_MERGE_LOCK_MAX:
            oldest = next(iter(_config_merge_locks))
            del _config_merge_locks[oldest]
        lock = asyncio.Lock()
        _config_merge_locks[key] = lock
    return lock


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

    async def merge_config_keys(
        self,
        session_id: uuid.UUID,
        updates: dict[str, Any] | None = None,
        *,
        remove: list[str] | None = None,
    ) -> SessionRead | None:
        """键级合并 config：单事务读-改-写 + 每会话锁。

        只触碰 updates/remove 指定的键，其余键保持原样——
        多调用方（checkpoint/goal 等）并发写不同键时不再互相覆盖。
        """
        async with _config_merge_lock(session_id):
            session = await self._get_session()
            try:
                result = await session.execute(
                    select(Session).where(Session.id == session_id)
                )
                obj = result.scalar_one_or_none()
                if not obj:
                    return None
                cfg = dict(obj.config) if isinstance(obj.config, dict) else {}
                for k, v in (updates or {}).items():
                    cfg[k] = v
                for k in remove or []:
                    cfg.pop(k, None)
                obj.config = cfg
                await self._maybe_commit(session)
                await session.refresh(obj)
                return SessionRead.model_validate(obj)
            except Exception:
                await session.rollback()
                raise
            finally:
                await self._close_session(session)

    async def list_by_user(self, user_id: uuid.UUID | str) -> list[SessionRead]:
        session = await self._get_session()
        try:
            uid = uuid.UUID(user_id) if isinstance(user_id, str) else user_id
            # 按最近活动排序（消息会刷新 updated_at）
            result = await session.execute(
                select(Session)
                .where(Session.user_id == uid)
                .order_by(Session.updated_at.desc(), Session.created_at.desc())
            )
            return [SessionRead.model_validate(obj) for obj in result.scalars().all()]
        except Exception:
            await session.rollback()
            raise
        finally:
            await self._close_session(session)
