"""Memory Graph API（Phase 1）— 只读查询

- GET /memory/graph/nodes：节点列表（q 关键词 / kind 过滤）
- GET /memory/graph/nodes/{node_id}：节点详情 + 关联边
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.repositories.memory_graph_repo import AsyncMemoryGraphRepository
from backend.schemas.user import UserRead

from ..dependencies import get_current_user

router = APIRouter(prefix="/memory/graph", tags=["MemoryGraph"])


class MemoryNodeRead(BaseModel):
    id: uuid.UUID
    kind: str
    title: str
    content: str
    tags: list[str]
    source: str
    confidence: float
    hit_count: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MemoryEdgeRead(BaseModel):
    id: uuid.UUID
    from_id: uuid.UUID
    to_id: uuid.UUID
    relation: str
    note: str

    model_config = {"from_attributes": True}


class MemoryNodeDetail(MemoryNodeRead):
    edges: list[MemoryEdgeRead] = []


@router.get("/nodes", response_model=list[MemoryNodeRead])
async def list_nodes(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    q: str = Query("", max_length=200),
    kind: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
) -> list[Any]:
    repo = AsyncMemoryGraphRepository()
    return await repo.recall(query=q, kind=kind or None, limit=limit, bump_hits=False)


@router.get("/nodes/{node_id}", response_model=MemoryNodeDetail)
async def get_node(
    node_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
) -> dict[str, Any]:
    repo = AsyncMemoryGraphRepository()
    node = await repo.get_node(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Memory node not found")
    edges = await repo.edges_of(node_id)
    data = {c.name: getattr(node, c.name) for c in node.__table__.columns}
    data["edges"] = edges
    return data
