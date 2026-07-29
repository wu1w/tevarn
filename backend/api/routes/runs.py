"""Agent Run API（Phase 0.5.2 Durable Execution）— 只读查询

- GET /runs/session/{session_id}：某会话的 run 列表（新→旧）
- GET /runs/{run_id}：单个 run 详情 + steps 流水
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.repositories.agent_run_repo import AsyncAgentRunRepository
from backend.schemas.user import UserRead

from ..dependencies import get_current_user

router = APIRouter(prefix="/runs", tags=["AgentRuns"])


class RunStepRead(BaseModel):
    id: uuid.UUID
    seq: int
    kind: str
    name: str
    status: str
    payload: dict[str, Any] | None
    duration_ms: float
    created_at: datetime

    model_config = {"from_attributes": True}


class RunSummary(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    status: str
    mode: str
    input_summary: str
    total_iterations: int
    total_tool_calls: int
    error: str | None
    started_at: datetime | None
    ended_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class RunDetail(RunSummary):
    final_summary: str
    meta: dict[str, Any] | None
    steps: list[RunStepRead] = []


@router.get("/session/{session_id}", response_model=list[RunSummary])
async def list_session_runs(
    session_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> list[Any]:
    repo = AsyncAgentRunRepository()
    return await repo.list_runs(session_id, limit=limit, offset=offset)


@router.get("/recent", response_model=list[RunSummary])
async def list_recent_runs(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    limit: int = Query(40, ge=1, le=100),
    status: str | None = Query(None),
) -> list[Any]:
    """0.5.3 I1：全局 Runs 入口，不依赖先开 chat。"""
    repo = AsyncAgentRunRepository()
    return await repo.list_recent(limit=limit, status=status)


@router.get("/{run_id}", response_model=RunDetail)
async def get_run(
    run_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
) -> dict[str, Any]:
    repo = AsyncAgentRunRepository()
    run = await repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    steps = await repo.list_steps(run_id)
    data = {
        **{c.name: getattr(run, c.name) for c in run.__table__.columns},
        "steps": steps,
    }
    return data
