"""
Skill Repository 接口与实现
处理技能的 CRUD 和启用状态管理
"""

import uuid
from abc import abstractmethod
from typing import Any

from sqlalchemy import select

from backend.models.skill import Skill

from .base import AsyncBaseRepository, BaseRepository


class SkillRepository(BaseRepository):
    """Skill 仓库接口"""

    @abstractmethod
    async def get_active_skills(self) -> list[Any]:
        """获取所有已启用的技能"""
        raise NotImplementedError

    @abstractmethod
    async def get_skill_by_name(self, name: str) -> Any | None:
        """根据名称获取技能"""
        raise NotImplementedError

    @abstractmethod
    async def toggle_skill(
        self, skill_id: uuid.UUID, enabled: bool
    ) -> Any | None:
        """切换技能启用状态"""
        raise NotImplementedError

    @abstractmethod
    async def get_skills_by_names(self, names: list[str]) -> list[Any]:
        """根据名称列表批量获取技能"""
        raise NotImplementedError


class AsyncSkillRepository(AsyncBaseRepository, SkillRepository):
    """基于 SQLAlchemy async session 的 Skill 仓库实现"""


    async def get_by_id(self, id: Any) -> Skill | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(Skill).where(Skill.id == id))
            return result.scalar_one_or_none()
        finally:
            await self._close_session(session)

    async def create(self, data: dict[str, Any]) -> Skill:
        session = await self._get_session()
        try:
            skill = Skill(**data)
            session.add(skill)
            await self._maybe_commit(session)
            await session.refresh(skill)
            return skill
        finally:
            await self._close_session(session)

    async def update(self, id: Any, data: dict[str, Any]) -> Skill | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(Skill).where(Skill.id == id))
            skill = result.scalar_one_or_none()
            if not skill:
                return None
            for key, value in data.items():
                setattr(skill, key, value)
            await self._maybe_commit(session)
            await session.refresh(skill)
            return skill
        finally:
            await self._close_session(session)

    async def delete(self, id: Any) -> bool:
        session = await self._get_session()
        try:
            result = await session.execute(select(Skill).where(Skill.id == id))
            skill = result.scalar_one_or_none()
            if not skill:
                return False
            await session.delete(skill)
            await self._maybe_commit(session)
            return True
        finally:
            await self._close_session(session)

    async def get_active_skills(self) -> list[Skill]:
        session = await self._get_session()
        try:
            result = await session.execute(select(Skill).where(Skill.enabled.is_(True)))
            return list(result.scalars().all())
        finally:
            await self._close_session(session)

    async def get_skill_by_name(self, name: str) -> Skill | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(Skill).where(Skill.name == name))
            return result.scalar_one_or_none()
        finally:
            await self._close_session(session)

    async def toggle_skill(self, skill_id: uuid.UUID, enabled: bool) -> Skill | None:
        return await self.update(skill_id, {"enabled": enabled})

    async def get_skills_by_names(self, names: list[str]) -> list[Skill]:
        session = await self._get_session()
        try:
            result = await session.execute(select(Skill).where(Skill.name.in_(names)))
            return list(result.scalars().all())
        finally:
            await self._close_session(session)
