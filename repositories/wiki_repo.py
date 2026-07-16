"""
Wiki Graph Repository 接口与实现
"""

import uuid
from abc import abstractmethod
from typing import Any

from sqlalchemy import select

from backend.models.wiki import WikiEntity, WikiRelation

from .base import AsyncBaseRepository, BaseRepository


class WikiEntityRepository(BaseRepository):
    """WikiEntity 仓库接口"""

    @abstractmethod
    async def list_all(self, limit: int = 500) -> list[Any]:
        """列出所有实体"""
        raise NotImplementedError

    @abstractmethod
    async def get_by_name(self, name: str) -> Any | None:
        """按名称获取实体"""
        raise NotImplementedError

    @abstractmethod
    async def search(self, query: str, limit: int = 20) -> list[Any]:
        """搜索实体"""
        raise NotImplementedError


class WikiRelationRepository(BaseRepository):
    """WikiRelation 仓库接口"""

    @abstractmethod
    async def list_by_source(self, source_id: uuid.UUID) -> list[Any]:
        """列出实体的所有出边关系"""
        raise NotImplementedError

    @abstractmethod
    async def list_by_target(self, target_id: uuid.UUID) -> list[Any]:
        """列出实体的所有入边关系"""
        raise NotImplementedError

    @abstractmethod
    async def get_between(
        self, source_id: uuid.UUID, target_id: uuid.UUID
    ) -> Any | None:
        """获取两个实体之间的关系"""
        raise NotImplementedError


class AsyncWikiEntityRepository(AsyncBaseRepository, WikiEntityRepository):
    """基于 SQLAlchemy async session 的 WikiEntity 仓库实现"""


    async def get_by_id(self, id: Any) -> WikiEntity | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(WikiEntity).where(WikiEntity.id == id))
            return result.scalar_one_or_none()
        finally:
            await self._close_session(session)

    async def create(self, data: dict[str, Any]) -> WikiEntity:
        session = await self._get_session()
        try:
            entity = WikiEntity(**data)
            session.add(entity)
            await self._maybe_commit(session)
            await session.refresh(entity)
            return entity
        finally:
            await self._close_session(session)

    async def update(self, id: Any, data: dict[str, Any]) -> WikiEntity | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(WikiEntity).where(WikiEntity.id == id))
            entity = result.scalar_one_or_none()
            if not entity:
                return None
            for key, value in data.items():
                setattr(entity, key, value)
            await self._maybe_commit(session)
            await session.refresh(entity)
            return entity
        finally:
            await self._close_session(session)

    async def delete(self, id: Any) -> bool:
        session = await self._get_session()
        try:
            result = await session.execute(select(WikiEntity).where(WikiEntity.id == id))
            entity = result.scalar_one_or_none()
            if not entity:
                return False
            await session.delete(entity)
            await self._maybe_commit(session)
            return True
        finally:
            await self._close_session(session)

    async def list_all(self, limit: int = 500) -> list[WikiEntity]:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(WikiEntity).order_by(WikiEntity.name).limit(limit)
            )
            return list(result.scalars().all())
        finally:
            await self._close_session(session)

    async def get_by_name(self, name: str) -> WikiEntity | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(WikiEntity).where(WikiEntity.name == name))
            return result.scalar_one_or_none()
        finally:
            await self._close_session(session)

    @staticmethod
    def _escape_like(value: str) -> str:
        """转义 LIKE 通配符，防止用户输入的 % / _ 匹配超出预期"""
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    async def search(self, query: str, limit: int = 20) -> list[WikiEntity]:
        session = await self._get_session()
        try:
            pattern = f"%{self._escape_like(query)}%"
            result = await session.execute(
                select(WikiEntity)
                .where(
                    WikiEntity.name.ilike(pattern, escape="\\")
                    | WikiEntity.description.ilike(pattern, escape="\\")
                )
                .limit(limit)
            )
            return list(result.scalars().all())
        finally:
            await self._close_session(session)


class AsyncWikiRelationRepository(AsyncBaseRepository, WikiRelationRepository):
    """基于 SQLAlchemy async session 的 WikiRelation 仓库实现"""


    async def get_by_id(self, id: Any) -> WikiRelation | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(WikiRelation).where(WikiRelation.id == id))
            return result.scalar_one_or_none()
        finally:
            await self._close_session(session)

    async def create(self, data: dict[str, Any]) -> WikiRelation:
        session = await self._get_session()
        try:
            rel = WikiRelation(**data)
            session.add(rel)
            await self._maybe_commit(session)
            await session.refresh(rel)
            return rel
        finally:
            await self._close_session(session)

    async def update(self, id: Any, data: dict[str, Any]) -> WikiRelation | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(WikiRelation).where(WikiRelation.id == id))
            rel = result.scalar_one_or_none()
            if not rel:
                return None
            for key, value in data.items():
                setattr(rel, key, value)
            await self._maybe_commit(session)
            await session.refresh(rel)
            return rel
        finally:
            await self._close_session(session)

    async def delete(self, id: Any) -> bool:
        session = await self._get_session()
        try:
            result = await session.execute(select(WikiRelation).where(WikiRelation.id == id))
            rel = result.scalar_one_or_none()
            if not rel:
                return False
            await session.delete(rel)
            await self._maybe_commit(session)
            return True
        finally:
            await self._close_session(session)

    async def list_by_source(self, source_id: uuid.UUID) -> list[WikiRelation]:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(WikiRelation).where(WikiRelation.source_id == source_id)
            )
            return list(result.scalars().all())
        finally:
            await self._close_session(session)

    async def list_by_target(self, target_id: uuid.UUID) -> list[WikiRelation]:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(WikiRelation).where(WikiRelation.target_id == target_id)
            )
            return list(result.scalars().all())
        finally:
            await self._close_session(session)

    async def get_between(
        self, source_id: uuid.UUID, target_id: uuid.UUID
    ) -> WikiRelation | None:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(WikiRelation).where(
                    WikiRelation.source_id == source_id,
                    WikiRelation.target_id == target_id,
                )
            )
            return result.scalar_one_or_none()
        finally:
            await self._close_session(session)
