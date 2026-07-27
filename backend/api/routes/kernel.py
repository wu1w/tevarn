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


# ── 身份系统（0.5 编制与档案）───────────────────────────────────


def _identity_registry():
    reg = get_kernel().identity_registry
    return reg


def _ident_dict(i) -> dict:
    return {
        "id": str(i.id),
        "name": i.name,
        "role": i.role,
        "status": i.status,
        "capabilities": i.capabilities,
        "credit_score": i.credit_score,
        "default_token_budget": i.default_token_budget,
        "sub_agent_id": str(i.sub_agent_id) if i.sub_agent_id else None,
        "created_at": i.created_at.isoformat() if i.created_at else None,
        "archived_at": i.archived_at.isoformat() if i.archived_at else None,
        "meta": i.meta or {},
    }


def _memory_dict(m) -> dict:
    return {
        "id": str(m.id),
        "identity_id": str(m.identity_id),
        "kind": m.kind,
        "content": m.content,
        "source": m.source,
        "approved_by": m.approved_by,
        "version": m.version,
        "superseded_by": str(m.superseded_by) if m.superseded_by else None,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


@router.get("/identities")
async def list_identities(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    status: str | None = Query(None),
):
    reg = _identity_registry()
    if reg is None:
        return {"error": "identity layer disabled", "identities": [], "total": 0}
    items = await reg.list(status=status)
    return {"identities": [_ident_dict(i) for i in items], "total": len(items)}


@router.post("/identities")
async def create_identity(
    body: dict,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    reg = _identity_registry()
    if reg is None:
        return {"error": "identity layer disabled"}
    name = str(body.get("name") or "").strip()
    if not name:
        return {"error": "name is required"}
    ident = await reg.create(
        name,
        role=str(body.get("role") or ""),
        capabilities=body.get("capabilities"),
        default_token_budget=body.get("default_token_budget"),
        user_id=current_user.id,
        meta=body.get("meta"),
    )
    return _ident_dict(ident)


@router.post("/identities/{identity_id}/transition")
async def transition_identity(
    identity_id: str,
    body: dict,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """状态机：suspend / resume / archive（archived 终态不可逆）。"""
    reg = _identity_registry()
    if reg is None:
        return {"error": "identity layer disabled"}
    action = str(body.get("action") or "")
    by = str(current_user.id)
    try:
        if action == "suspend":
            ident = await reg.suspend(identity_id, by=by)
        elif action == "resume":
            ident = await reg.resume(identity_id, by=by)
        elif action == "archive":
            ident = await reg.archive(identity_id, by=by)
        else:
            return {"error": f"unknown action {action!r}（suspend/resume/archive）"}
    except ValueError as e:
        return {"error": str(e)}
    return _ident_dict(ident)


@router.post("/identities/{identity_id}/capabilities")
async def set_identity_capabilities(
    identity_id: str,
    body: dict,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """权限档案变更——全程审计（禁止静默改权）。"""
    reg = _identity_registry()
    if reg is None:
        return {"error": "identity layer disabled"}
    try:
        ident = await reg.set_capabilities(
            identity_id, body.get("capabilities"), by=str(current_user.id)
        )
    except ValueError as e:
        return {"error": str(e)}
    return _ident_dict(ident)


@router.get("/identities/{identity_id}/memory")
async def get_identity_memory(
    identity_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    kind: str | None = Query(None),
):
    reg = _identity_registry()
    if reg is None:
        return {"error": "identity layer disabled", "memory": [], "total": 0}
    items = await reg.current_memory(identity_id, kind=kind)
    return {"memory": [_memory_dict(m) for m in items], "total": len(items)}


@router.post("/identities/{identity_id}/memory")
async def add_identity_memory(
    identity_id: str,
    body: dict,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    reg = _identity_registry()
    if reg is None:
        return {"error": "identity layer disabled"}
    try:
        entry = await reg.add_memory(
            identity_id,
            str(body.get("kind") or ""),
            str(body.get("content") or ""),
            source=str(body.get("source") or "manual"),
            approved_by=body.get("approved_by"),
        )
    except ValueError as e:
        return {"error": str(e)}
    return _memory_dict(entry)


@router.post("/identities/{identity_id}/memory/{entry_id}/supersede")
async def supersede_identity_memory(
    identity_id: str,
    entry_id: str,
    body: dict,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    reg = _identity_registry()
    if reg is None:
        return {"error": "identity layer disabled"}
    try:
        entry = await reg.supersede_memory(
            entry_id,
            str(body.get("content") or ""),
            approved_by=str(body.get("approved_by") or current_user.id),
        )
    except ValueError as e:
        return {"error": str(e)}
    return _memory_dict(entry)
