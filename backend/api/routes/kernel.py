"""Agent Kernel 观测 API（阶段 1/W2）。

Security Console 数据源：当前进程树（能力/预算/状态）+ 中介审计事件。
只读接口——进程生命周期由 loop 驱动，不接受外部写操作。

0.4.1：新增提权交互端点（escalations）——这是控制台唯一的写入口，
对应「用户授权是唯一合法的能力扩大通道」。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from ..dependencies import get_current_user
from backend.kernel import get_kernel
from backend.schemas.user import UserRead

router = APIRouter(prefix="/kernel", tags=["kernel"])


@router.get("/processes")
async def list_processes(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    include_terminal: bool = Query(False),
):
    kernel = get_kernel()
    procs = kernel.list_processes(include_terminal=include_terminal)
    return {
        "enabled": True,
        "processes": [p.to_dict() for p in procs],
        "total": len(procs),
    }


@router.get("/processes/{process_id}")
async def get_process(
    process_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    kernel = get_kernel()
    proc = kernel.get_process(process_id)
    if proc is None:
        return {"error": "process not found", "process_id": process_id}
    data = proc.to_dict()
    if proc.token is not None:
        data["token"] = proc.token.to_dict()
    return data


@router.get("/events")
async def list_events(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    process_id: str | None = Query(None),
    kind: str | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
):
    kernel = get_kernel()
    events = kernel.events(process_id=process_id, kind=kind, limit=limit)
    return {
        "events": [e.to_dict() for e in events],
        "total": len(events),
    }


# ── 提权交互（0.4.1）──────────────────────────────────────────


@router.get("/escalations")
async def list_escalations(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    status: str | None = Query(None),
):
    kernel = get_kernel()
    reqs = kernel.list_escalations(status=status)
    return {
        "escalations": [r.to_dict() for r in reqs],
        "total": len(reqs),
    }


@router.post("/escalations/{request_id}/approve")
async def approve_escalation(
    request_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    kernel = get_kernel()
    try:
        req = await kernel.approve_escalation(request_id, by=str(current_user.id))
    except ValueError as e:
        return {"error": str(e), "request_id": request_id}
    return req.to_dict()


@router.post("/escalations/{request_id}/deny")
async def deny_escalation(
    request_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    kernel = get_kernel()
    try:
        req = await kernel.deny_escalation(request_id, by=str(current_user.id))
    except ValueError as e:
        return {"error": str(e), "request_id": request_id}
    return req.to_dict()
