"""Entity API — 长期记忆实体管理"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.core.unit_of_work import UnitOfWork
from backend.repositories.entity_repo import EntityRepository
from backend.schemas.user import UserRead

from ..dependencies import get_current_user

router = APIRouter(prefix="/entities", tags=["Entities"])


class EntityCreate(BaseModel):
    name: str
    entity_type: str = "custom"
    attributes: dict[str, Any] = {}
    description: str = ""


class EntityUpdate(BaseModel):
    name: str | None = None
    entity_type: str | None = None
    attributes: dict[str, Any] | None = None
    description: str | None = None
    status: str | None = None


class EntityRead(BaseModel):
    id: uuid.UUID
    name: str
    entity_type: str
    attributes: dict[str, Any]
    description: str
    mention_count: int
    first_mentioned_at: str | None
    last_mentioned_at: str | None
    status: str
    created_at: str

    model_config = {"from_attributes": True}


@router.get("", response_model=list[EntityRead])
async def list_entities(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    entity_type: Annotated[str | None, Query()] = None,
    status: Annotated[str, Query()] = "active",
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """列出用户的实体记忆"""
    async with UnitOfWork() as uow:
        repo = EntityRepository(uow.session)
        entities = await repo.list_by_user(
            current_user.id,
            entity_type=entity_type,
            status=status,
            limit=limit,
            offset=offset,
        )
        return [
            EntityRead(
                id=e.id,
                name=e.name,
                entity_type=e.entity_type,
                attributes=e.attributes or {},
                description=e.description or "",
                mention_count=e.mention_count,
                first_mentioned_at=e.first_mentioned_at,
                last_mentioned_at=e.last_mentioned_at,
                status=e.status,
                created_at=e.created_at.isoformat() if e.created_at else "",
            )
            for e in entities
        ]


@router.get("/search", response_model=list[EntityRead])
async def search_entities(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    q: Annotated[str, Query(min_length=1, max_length=100)],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
):
    """搜索实体记忆"""
    async with UnitOfWork() as uow:
        repo = EntityRepository(uow.session)
        entities = await repo.search(q, user_id=current_user.id, limit=limit)
        return [
            EntityRead(
                id=e.id,
                name=e.name,
                entity_type=e.entity_type,
                attributes=e.attributes or {},
                description=e.description or "",
                mention_count=e.mention_count,
                first_mentioned_at=e.first_mentioned_at,
                last_mentioned_at=e.last_mentioned_at,
                status=e.status,
                created_at=e.created_at.isoformat() if e.created_at else "",
            )
            for e in entities
        ]


@router.post("", response_model=EntityRead, status_code=201)
async def create_entity(
    data: EntityCreate,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """手动创建实体记忆"""
    async with UnitOfWork() as uow:
        repo = EntityRepository(uow.session)
        existing = await repo.get_by_name(data.name, user_id=current_user.id)
        if existing:
            raise HTTPException(status_code=409, detail=f"Entity '{data.name}' already exists")
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        entity = await repo.create({
            "user_id": current_user.id,
            "name": data.name,
            "entity_type": data.entity_type,
            "attributes": data.attributes,
            "description": data.description,
            "first_mentioned_at": now,
            "last_mentioned_at": now,
        })
        return EntityRead(
            id=entity.id,
            name=entity.name,
            entity_type=entity.entity_type,
            attributes=entity.attributes or {},
            description=entity.description or "",
            mention_count=entity.mention_count,
            first_mentioned_at=entity.first_mentioned_at,
            last_mentioned_at=entity.last_mentioned_at,
            status=entity.status,
            created_at=entity.created_at.isoformat() if entity.created_at else "",
        )


@router.get("/{entity_id}", response_model=EntityRead)
async def get_entity(
    entity_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """获取实体详情"""
    async with UnitOfWork() as uow:
        repo = EntityRepository(uow.session)
        e = await repo.get_by_id(entity_id)
        if not e:
            raise HTTPException(status_code=404, detail="Entity not found")
        return EntityRead(
            id=e.id,
            name=e.name,
            entity_type=e.entity_type,
            attributes=e.attributes or {},
            description=e.description or "",
            mention_count=e.mention_count,
            first_mentioned_at=e.first_mentioned_at,
            last_mentioned_at=e.last_mentioned_at,
            status=e.status,
            created_at=e.created_at.isoformat() if e.created_at else "",
        )


@router.put("/{entity_id}", response_model=EntityRead)
async def update_entity(
    entity_id: uuid.UUID,
    data: EntityUpdate,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """更新实体"""
    async with UnitOfWork() as uow:
        repo = EntityRepository(uow.session)
        update_data = data.model_dump(exclude_none=True)
        e = await repo.update(entity_id, update_data)
        if not e:
            raise HTTPException(status_code=404, detail="Entity not found")
        return EntityRead(
            id=e.id,
            name=e.name,
            entity_type=e.entity_type,
            attributes=e.attributes or {},
            description=e.description or "",
            mention_count=e.mention_count,
            first_mentioned_at=e.first_mentioned_at,
            last_mentioned_at=e.last_mentioned_at,
            status=e.status,
            created_at=e.created_at.isoformat() if e.created_at else "",
        )


@router.delete("/{entity_id}")
async def delete_entity(
    entity_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """删除实体记忆"""
    async with UnitOfWork() as uow:
        repo = EntityRepository(uow.session)
        ok = await repo.delete(entity_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Entity not found")
        return {"ok": True}


@router.post("/{source_id}/merge/{target_id}")
async def merge_entities(
    source_id: uuid.UUID,
    target_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """合并两个实体"""
    async with UnitOfWork() as uow:
        repo = EntityRepository(uow.session)
        ok = await repo.merge(source_id, target_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Entity not found")
        return {"ok": True}
