"""Entity 仓库 — 长期记忆实体存取"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, desc, or_
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.entity import Entity


class EntityRepository:
    """实体记忆的异步存取"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, data: dict[str, Any]) -> Entity:
        entity = Entity(**data)
        self.db.add(entity)
        await self.db.flush()
        return entity

    async def get_by_id(self, entity_id: uuid.UUID) -> Entity | None:
        result = await self.db.execute(
            select(Entity).where(Entity.id == entity_id)
        )
        return result.scalar_one_or_none()

    async def get_by_name(
        self, name: str, user_id: uuid.UUID | None = None
    ) -> Entity | None:
        q = select(Entity).where(Entity.name == name)
        if user_id:
            q = q.where(Entity.user_id == user_id)
        q = q.limit(1)
        result = await self.db.execute(q)
        return result.scalar_one_or_none()

    async def list_by_user(
        self,
        user_id: uuid.UUID,
        entity_type: str | None = None,
        status: str = "active",
        limit: int = 200,
        offset: int = 0,
    ) -> list[Entity]:
        q = select(Entity).where(Entity.user_id == user_id)
        if entity_type:
            q = q.where(Entity.entity_type == entity_type)
        if status:
            q = q.where(Entity.status == status)
        q = q.order_by(desc(Entity.last_mentioned_at), desc(Entity.mention_count))
        q = q.limit(limit).offset(offset)
        result = await self.db.execute(q)
        return list(result.scalars().all())

    async def search(
        self,
        query: str,
        user_id: uuid.UUID | None = None,
        limit: int = 10,
    ) -> list[Entity]:
        """简单文本搜索实体（名称/描述）"""
        pattern = f"%{query}%"
        q = select(Entity).where(
            or_(
                Entity.name.ilike(pattern),
                Entity.description.ilike(pattern),
            )
        )
        if user_id:
            q = q.where(Entity.user_id == user_id)
        q = q.where(Entity.status == "active")
        q = q.order_by(desc(Entity.mention_count)).limit(limit)
        result = await self.db.execute(q)
        return list(result.scalars().all())

    async def update(self, entity_id: uuid.UUID, data: dict[str, Any]) -> Entity | None:
        entity = await self.get_by_id(entity_id)
        if not entity:
            return None
        for k, v in data.items():
            if hasattr(entity, k):
                setattr(entity, k, v)
        await self.db.flush()
        return entity

    async def delete(self, entity_id: uuid.UUID) -> bool:
        entity = await self.get_by_id(entity_id)
        if not entity:
            return False
        await self.db.delete(entity)
        return True

    async def merge(self, source_id: uuid.UUID, target_id: uuid.UUID) -> bool:
        """合并两个实体"""
        source = await self.get_by_id(source_id)
        target = await self.get_by_id(target_id)
        if not source or not target:
            return False
        target.mention_count += source.mention_count
        if source.attributes:
            target.attributes = {**(source.attributes or {}), **(target.attributes or {})}
        if source.description and not target.description:
            target.description = source.description
        await self.db.delete(source)
        return True
