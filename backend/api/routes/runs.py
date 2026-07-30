"""Agent Run API（Phase 0.5.2 + Phase 2.1 统一 Run）— 只读查询

- GET /runs：全局列表（origin/status 过滤）— Phase 2 统一入口
- GET /runs/recent：兼容别名
- GET /runs/session/{session_id}：某会话的 run 列表（新→旧）
- GET /runs/{run_id}：单个 run 详情 + steps 流水
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.agent.run_lifecycle import public_status
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
    public_status: str = ""
    mode: str
    origin: str = "chat"
    identity_id: uuid.UUID | None = None
    parent_run_id: uuid.UUID | None = None
    input_summary: str
    total_iterations: int
    total_tool_calls: int
    token_limit: int = 0
    token_used: int = 0
    error: str | None
    started_at: datetime | None
    ended_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class RunDetail(RunSummary):
    final_summary: str
    meta: dict[str, Any] | None
    checkpoint: dict[str, Any] | None = None
    steps: list[RunStepRead] = []


def _enrich(run: Any) -> dict[str, Any]:
    data = {c.name: getattr(run, c.name) for c in run.__table__.columns}
    data["public_status"] = public_status(str(data.get("status") or ""))
    data.setdefault("origin", "chat")
    data.setdefault("token_limit", 0)
    data.setdefault("token_used", 0)
    return data


@router.get("", response_model=list[RunSummary])
@router.get("/", response_model=list[RunSummary], include_in_schema=False)
async def list_runs(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    limit: int = Query(40, ge=1, le=100),
    status: str | None = Query(None, description="内部细粒度 status"),
    origin: str | None = Query(
        None, description="chat|inbox|cron|cluster|subagent|headless"
    ),
    identity_id: uuid.UUID | None = Query(None),
) -> list[dict[str, Any]]:
    """Phase 2 统一入口：全部 origin 的 Run 列表。"""
    repo = AsyncAgentRunRepository()
    rows = await repo.list_recent(
        limit=limit, status=status, origin=origin, identity_id=identity_id
    )
    return [_enrich(r) for r in rows]


@router.get("/session/{session_id}", response_model=list[RunSummary])
async def list_session_runs(
    session_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    repo = AsyncAgentRunRepository()
    rows = await repo.list_runs(session_id, limit=limit, offset=offset)
    return [_enrich(r) for r in rows]


@router.get("/recent", response_model=list[RunSummary])
async def list_recent_runs(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    limit: int = Query(40, ge=1, le=100),
    status: str | None = Query(None),
    origin: str | None = Query(None),
) -> list[dict[str, Any]]:
    """兼容 0.5.3 入口；等价 GET /runs。"""
    repo = AsyncAgentRunRepository()
    rows = await repo.list_recent(limit=limit, status=status, origin=origin)
    return [_enrich(r) for r in rows]


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
    data = _enrich(run)
    data["steps"] = steps
    return data
