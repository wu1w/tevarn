"""Trace API — 透明化 Agent 轨迹查询"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.core.unit_of_work import UnitOfWork
from backend.repositories.trace_repo import TraceRepository
from backend.schemas.user import UserRead

from ..dependencies import get_current_user

router = APIRouter(prefix="/traces", tags=["Traces"])


class TraceRead(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    message_id: str | None
    thinking_steps: list[dict[str, Any]]
    tool_calls_trace: list[dict[str, Any]]
    rag_sources: list[dict[str, Any]]
    cluster_info: dict[str, Any] | None
    total_iterations: int
    total_tool_calls: int
    total_tokens: int
    duration_ms: float
    user_input_summary: str
    status: str
    created_at: str

    model_config = {"from_attributes": True}


class TraceSummary(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    total_iterations: int
    total_tool_calls: int
    duration_ms: float
    user_input_summary: str
    status: str
    created_at: str


@router.get("/session/{session_id}", response_model=list[TraceSummary])
async def list_traces_by_session(
    session_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """获取会话的轨迹列表（摘要）"""
    async with UnitOfWork() as uow:
        repo = TraceRepository(uow.session)
        traces = await repo.list_by_session(session_id, limit=limit, offset=offset)
        return [
            TraceSummary(
                id=t.id,
                session_id=t.session_id,
                total_iterations=t.total_iterations,
                total_tool_calls=t.total_tool_calls,
                duration_ms=t.duration_ms,
                user_input_summary=t.user_input_summary,
                status=t.status,
                created_at=t.created_at.isoformat() if t.created_at else "",
            )
            for t in traces
        ]


@router.get("/session/{session_id}/latest", response_model=TraceRead | None)
async def get_latest_trace(
    session_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """获取会话最新一次运行的完整轨迹"""
    async with UnitOfWork() as uow:
        repo = TraceRepository(uow.session)
        t = await repo.get_latest_by_session(session_id)
        if not t:
            return None
        return TraceRead(
            id=t.id,
            session_id=t.session_id,
            message_id=t.message_id,
            thinking_steps=t.thinking_steps or [],
            tool_calls_trace=t.tool_calls_trace or [],
            rag_sources=t.rag_sources or [],
            cluster_info=t.cluster_info,
            total_iterations=t.total_iterations,
            total_tool_calls=t.total_tool_calls,
            total_tokens=t.total_tokens,
            duration_ms=t.duration_ms,
            user_input_summary=t.user_input_summary,
            status=t.status,
            created_at=t.created_at.isoformat() if t.created_at else "",
        )


@router.get("/{trace_id}", response_model=TraceRead)
async def get_trace(
    trace_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """获取单条轨迹详情"""
    async with UnitOfWork() as uow:
        repo = TraceRepository(uow.session)
        t = await repo.get_by_id(trace_id)
        if not t:
            raise HTTPException(status_code=404, detail="Trace not found")
        return TraceRead(
            id=t.id,
            session_id=t.session_id,
            message_id=t.message_id,
            thinking_steps=t.thinking_steps or [],
            tool_calls_trace=t.tool_calls_trace or [],
            rag_sources=t.rag_sources or [],
            cluster_info=t.cluster_info,
            total_iterations=t.total_iterations,
            total_tool_calls=t.total_tool_calls,
            total_tokens=t.total_tokens,
            duration_ms=t.duration_ms,
            user_input_summary=t.user_input_summary,
            status=t.status,
            created_at=t.created_at.isoformat() if t.created_at else "",
        )


@router.delete("/{trace_id}")
async def delete_trace(
    trace_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """删除轨迹记录"""
    async with UnitOfWork() as uow:
        repo = TraceRepository(uow.session)
        ok = await repo.delete(trace_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Trace not found")
        return {"ok": True}
