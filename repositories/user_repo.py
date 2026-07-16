"""
User Repository 接口与实现
"""

import uuid
from abc import abstractmethod
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from backend.models.user import User
from backend.schemas.user import UserRead

from .base import AsyncBaseRepository, BaseRepository


class UserRepository(BaseRepository):
    """User 仓库接口"""

    @abstractmethod
    async def get_by_email(self, email: str) -> Any | None:
        """通过邮箱获取用户"""
        raise NotImplementedError

    @abstractmethod
    async def get_by_username(self, username: str) -> Any | None:
        """通过用户名获取用户"""
        raise NotImplementedError

    @abstractmethod
    async def update_last_login(self, user_id: uuid.UUID) -> Any | None:
        """更新最后登录时间"""
        raise NotImplementedError


class AsyncUserRepository(AsyncBaseRepository, UserRepository):
    """基于 SQLAlchemy async session 的 User 仓库实现"""


    async def get_by_id(self, id: Any) -> UserRead | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(User).where(User.id == id))
            user = result.scalar_one_or_none()
            return UserRead.model_validate(user) if user is not None else None
        finally:
            await self._close_session(session)

    async def create(self, data: dict[str, Any]) -> UserRead:
        session = await self._get_session()
        try:
            user = User(**data)
            session.add(user)
            await self._maybe_commit(session)
            await session.refresh(user)
            return UserRead.model_validate(user)
        finally:
            await self._close_session(session)

    async def update(self, id: Any, data: dict[str, Any]) -> UserRead | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(User).where(User.id == id))
            user = result.scalar_one_or_none()
            if not user:
                return None
            for key, value in data.items():
                setattr(user, key, value)
            await self._maybe_commit(session)
            await session.refresh(user)
            return UserRead.model_validate(user)
        finally:
            await self._close_session(session)

    async def delete(self, id: Any) -> bool:
        session = await self._get_session()
        try:
            result = await session.execute(select(User).where(User.id == id))
            user = result.scalar_one_or_none()
            if not user:
                return False
            await session.delete(user)
            await self._maybe_commit(session)
            return True
        finally:
            await self._close_session(session)

    async def get_by_email(self, email: str) -> UserRead | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(User).where(User.email == email))
            user = result.scalar_one_or_none()
            return UserRead.model_validate(user) if user is not None else None
        finally:
            await self._close_session(session)

    async def get_by_username(self, username: str) -> UserRead | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(User).where(User.username == username))
            user = result.scalar_one_or_none()
            return UserRead.model_validate(user) if user is not None else None
        finally:
            await self._close_session(session)

    async def update_last_login(self, user_id: uuid.UUID) -> UserRead | None:
        return await self.update(user_id, {"last_login_at": datetime.now(timezone.utc)})

    async def count(self) -> int:
        """统计用户总数"""
        session = await self._get_session()
        try:
            result = await session.execute(select(User))
            users = result.scalars().all()
            return len(users)
        finally:
            await self._close_session(session)
