"""
Notification Repository 接口与实现
"""

import uuid
from abc import abstractmethod
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, update

from backend.models.notification import Notification
from backend.schemas.notification import NotificationRead

from .base import AsyncBaseRepository, BaseRepository


class NotificationRepository(BaseRepository):
    """Notification 仓库接口"""

    @abstractmethod
    async def list_by_user(
        self,
        user_id: uuid.UUID,
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Any]:
        """列出用户的通知"""
        raise NotImplementedError

    @abstractmethod
    async def count_by_user(self, user_id: uuid.UUID, unread_only: bool = False) -> int:
        """统计用户通知总数（不受 limit/offset 影响）"""
        raise NotImplementedError

    @abstractmethod
    async def mark_as_read(
        self, notification_id: uuid.UUID, user_id: uuid.UUID | None = None
    ) -> Any | None:
        """标记通知为已读；传入 user_id 时仅当通知属于该用户才会生效"""
        raise NotImplementedError

    @abstractmethod
    async def mark_all_as_read(self, user_id: uuid.UUID) -> int:
        """标记用户所有通知为已读，返回更新的数量"""
        raise NotImplementedError

    @abstractmethod
    async def get_unread_count(self, user_id: uuid.UUID) -> int:
        """获取用户未读通知数量"""
        raise NotImplementedError


class AsyncNotificationRepository(AsyncBaseRepository, NotificationRepository):
    """基于 SQLAlchemy async session 的 Notification 仓库实现"""


    async def get_by_id(self, id: Any) -> NotificationRead | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(Notification).where(Notification.id == id))
            obj = result.scalar_one_or_none()
            return NotificationRead.model_validate(obj) if obj is not None else None
        finally:
            await self._close_session(session)

    async def create(self, data: dict[str, Any]) -> NotificationRead:
        session = await self._get_session()
        try:
            obj = Notification(**data)
            session.add(obj)
            await self._maybe_commit(session)
            await session.refresh(obj)
            return NotificationRead.model_validate(obj)
        finally:
            await self._close_session(session)

    async def update(self, id: Any, data: dict[str, Any]) -> NotificationRead | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(Notification).where(Notification.id == id))
            obj = result.scalar_one_or_none()
            if not obj:
                return None
            for key, value in data.items():
                setattr(obj, key, value)
            await self._maybe_commit(session)
            await session.refresh(obj)
            return NotificationRead.model_validate(obj)
        finally:
            await self._close_session(session)

    async def delete(self, id: Any) -> bool:
        session = await self._get_session()
        try:
            result = await session.execute(select(Notification).where(Notification.id == id))
            obj = result.scalar_one_or_none()
            if not obj:
                return False
            await session.delete(obj)
            await self._maybe_commit(session)
            return True
        finally:
            await self._close_session(session)

    async def list_by_user(
        self,
        user_id: uuid.UUID,
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[NotificationRead]:
        session = await self._get_session()
        try:
            stmt = select(Notification).where(Notification.user_id == user_id)
            if unread_only:
                stmt = stmt.where(Notification.is_read.is_(False))
            stmt = stmt.order_by(Notification.created_at.desc()).limit(limit).offset(offset)
            result = await session.execute(stmt)
            return [NotificationRead.model_validate(n) for n in result.scalars().all()]
        finally:
            await self._close_session(session)

    async def count_by_user(self, user_id: uuid.UUID, unread_only: bool = False) -> int:
        session = await self._get_session()
        try:
            stmt = select(func.count()).select_from(Notification).where(Notification.user_id == user_id)
            if unread_only:
                stmt = stmt.where(Notification.is_read.is_(False))
            result = await session.execute(stmt)
            return result.scalar() or 0
        finally:
            await self._close_session(session)

    async def mark_as_read(
        self, notification_id: uuid.UUID, user_id: uuid.UUID | None = None
    ) -> NotificationRead | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(Notification).where(Notification.id == notification_id))
            obj = result.scalar_one_or_none()
            if obj is None:
                return None
            if user_id is not None and obj.user_id != user_id:
                # 不属于该用户的通知，拒绝修改（调用方应视为 404/403）
                return None
            obj.is_read = True
            obj.read_at = datetime.now(timezone.utc)
            await self._maybe_commit(session)
            await session.refresh(obj)
            return NotificationRead.model_validate(obj)
        finally:
            await self._close_session(session)

    async def mark_all_as_read(self, user_id: uuid.UUID) -> int:
        session = await self._get_session()
        try:
            # 使用批量 UPDATE 代替逐条读改写，兼具原子性与性能
            result = await session.execute(
                update(Notification)
                .where(Notification.user_id == user_id, Notification.is_read.is_(False))
                .values(is_read=True, read_at=datetime.now(timezone.utc))
            )
            await self._maybe_commit(session)
            return result.rowcount or 0
        finally:
            await self._close_session(session)

    async def get_unread_count(self, user_id: uuid.UUID) -> int:
        return await self.count_by_user(user_id, unread_only=True)
