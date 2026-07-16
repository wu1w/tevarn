"""
Agent Profile Repository 接口与实现
"""

import uuid
from abc import abstractmethod
from typing import Any

from sqlalchemy import or_, select, update

from backend.models.agent_profile import AgentProfile

from .base import AsyncBaseRepository, BaseRepository


class AgentProfileRepository(BaseRepository):
    """AgentProfile 仓库接口"""

    @abstractmethod
    async def list_all(self) -> list[Any]:
        """列出所有 Agent 配置"""
        raise NotImplementedError

    @abstractmethod
    async def get_by_name(self, name: str) -> Any | None:
        """按名称获取配置"""
        raise NotImplementedError

    @abstractmethod
    async def list_by_user(self, user_id: uuid.UUID) -> list[Any]:
        """列出当前用户可见的配置（含全局共享）"""
        raise NotImplementedError

    @abstractmethod
    async def get_default(self, user_id: uuid.UUID | None = None) -> Any | None:
        """
        获取默认配置。
        优先返回指定用户的默认配置；若不存在，则回退到全局默认配置。
        """
        raise NotImplementedError

    @abstractmethod
    async def set_default(
        self,
        profile_id: uuid.UUID,
        current_user_id: uuid.UUID | None = None,
    ) -> Any | None:
        """
        设置默认配置（按用户范围取消其他配置的默认标记）。
        - 若目标配置为私有（user_id 非空），则只在同一用户范围内取消默认。
        - 若目标配置为全局（user_id 为空），则只在全局范围内取消默认。
        """
        raise NotImplementedError


class AsyncAgentProfileRepository(AsyncBaseRepository, AgentProfileRepository):
    """基于 SQLAlchemy async session 的 AgentProfile 仓库实现"""


    async def get_by_id(self, id: Any) -> AgentProfile | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(AgentProfile).where(AgentProfile.id == id))
            return result.scalar_one_or_none()
        finally:
            await self._close_session(session)

    async def create(self, data: dict[str, Any]) -> AgentProfile:
        session = await self._get_session()
        try:
            profile = AgentProfile(**data)
            session.add(profile)
            await self._maybe_commit(session)
            await session.refresh(profile)
            return profile
        finally:
            await self._close_session(session)

    async def update(self, id: Any, data: dict[str, Any]) -> AgentProfile | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(AgentProfile).where(AgentProfile.id == id))
            profile = result.scalar_one_or_none()
            if not profile:
                return None
            for key, value in data.items():
                setattr(profile, key, value)
            await self._maybe_commit(session)
            await session.refresh(profile)
            return profile
        finally:
            await self._close_session(session)

    async def delete(self, id: Any) -> bool:
        session = await self._get_session()
        try:
            result = await session.execute(select(AgentProfile).where(AgentProfile.id == id))
            profile = result.scalar_one_or_none()
            if not profile:
                return False
            await session.delete(profile)
            await self._maybe_commit(session)
            return True
        finally:
            await self._close_session(session)

    async def list_all(self) -> list[AgentProfile]:
        session = await self._get_session()
        try:
            result = await session.execute(select(AgentProfile).order_by(AgentProfile.name))
            return list(result.scalars().all())
        finally:
            await self._close_session(session)

    async def list_by_user(self, user_id: uuid.UUID) -> list[AgentProfile]:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(AgentProfile)
                .where(
                    or_(
                        AgentProfile.user_id == user_id,
                        AgentProfile.user_id.is_(None),
                    )
                )
                .order_by(AgentProfile.name)
            )
            return list(result.scalars().all())
        finally:
            await self._close_session(session)

    async def get_by_name(self, name: str) -> AgentProfile | None:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(AgentProfile).where(AgentProfile.name == name)
            )
            return result.scalar_one_or_none()
        finally:
            await self._close_session(session)

    async def get_default(
        self, user_id: uuid.UUID | None = None
    ) -> AgentProfile | None:
        """
        获取默认配置。
        优先返回指定用户的默认配置；若不存在，则回退到全局默认配置（user_id 为 None）。
        """
        session = await self._get_session()
        try:
            if user_id is not None:
                result = await session.execute(
                    select(AgentProfile).where(
                        AgentProfile.user_id == user_id,
                        AgentProfile.is_default.is_(True),
                    )
                )
                profile = result.scalar_one_or_none()
                if profile is not None:
                    return profile
            result = await session.execute(
                select(AgentProfile).where(
                    AgentProfile.user_id.is_(None),
                    AgentProfile.is_default.is_(True),
                )
            )
            return result.scalar_one_or_none()
        finally:
            await self._close_session(session)

    async def set_default(
        self,
        profile_id: uuid.UUID,
        current_user_id: uuid.UUID | None = None,
    ) -> AgentProfile | None:
        """
        原子化设置默认配置（按用户范围隔离）。
        - 目标配置的 user_id 非空时，仅在该用户范围内取消其他默认。
        - 目标配置的 user_id 为空时，仅在全局范围（user_id 为空）内取消其他默认。
        通过行级锁 + 带 WHERE 条件的批量 UPDATE 消除并发竞态。
        """
        session = await self._get_session()
        try:
            # 1. 加载目标行并加锁
            target_result = await session.execute(
                select(AgentProfile)
                .where(AgentProfile.id == profile_id)
                .with_for_update()
            )
            target = target_result.scalar_one_or_none()
            if target is None:
                return None

            # 2. 根据目标配置的归属确定作用范围
            scope_user_id = target.user_id

            # 3. 仅对同范围内的非目标行取消默认标记
            unset_filter = [AgentProfile.id != profile_id]
            if scope_user_id is None:
                unset_filter.append(AgentProfile.user_id.is_(None))
            else:
                unset_filter.append(AgentProfile.user_id == scope_user_id)
            await session.execute(
                update(AgentProfile)
                .where(*unset_filter)
                .values(is_default=False)
            )

            # 4. 设置目标行为默认
            target.is_default = True
            await self._maybe_commit(session)
            await session.refresh(target)
            return target
        finally:
            await self._close_session(session)
