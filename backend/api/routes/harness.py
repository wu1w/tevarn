"""Harness APIs: plan gate, permission rules, headless, workflow, rewind, sandbox profiles.

Kept small and separate from the large kernel.py router.
"""

from __future__ import annotations

from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.api.dependencies import get_current_user
from backend.schemas.user import UserRead

router = APIRouter(prefix="/kernel/harness", tags=["harness"])


# ── Permission rules ──────────────────────────────────────────


class PermissionRulesBody(BaseModel):
    allow: list[str] = Field(default_factory=list)
    ask: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)


@router.get("/permission-rules")
async def get_permission_rules(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    from backend.agent.permission_overlay import describe_rules, load_user_rules_payload

    return {"rules": load_user_rules_payload(), **describe_rules()}


@router.put("/permission-rules")
async def put_permission_rules(
    body: PermissionRulesBody,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """Persist allow/ask/deny DSL into runtime settings (process-level)."""
    from backend.core.config import settings

    payload = {
        "allow": list(body.allow or []),
        "ask": list(body.ask or []),
        "deny": list(body.deny or []),
    }
    import json

    settings.agent_permission_rules_json = json.dumps(payload, ensure_ascii=False)  # type: ignore[attr-defined]
    settings.agent_permission_allow = payload["allow"]  # type: ignore[attr-defined]
    settings.agent_permission_ask = payload["ask"]  # type: ignore[attr-defined]
    settings.agent_permission_deny = payload["deny"]  # type: ignore[attr-defined]
    # best-effort DB persist
    try:
        from backend.repositories.setting_repo import AsyncSettingRepository

        repo = AsyncSettingRepository()
        await repo.upsert("agent_permission_rules", payload)
    except Exception:
        pass
    return {"ok": True, "rules": payload}


# ── Plan gate ─────────────────────────────────────────────────


class PlanBody(BaseModel):
    markdown: str = ""
    session_id: Optional[str] = None
    job_id: Optional[str] = None


class PlanKeyBody(BaseModel):
    session_id: Optional[str] = None
    job_id: Optional[str] = None


@router.get("/plan")
async def get_plan(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    session_id: Optional[str] = None,
    job_id: Optional[str] = None,
):
    from backend.agent.plan_session import plan_snapshot

    return plan_snapshot(session_id=session_id, job_id=job_id)


@router.post("/plan")
async def submit_plan(
    body: PlanBody,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    from backend.agent.plan_session import submit_plan_markdown

    return submit_plan_markdown(
        body.markdown, session_id=body.session_id, job_id=body.job_id
    )


@router.post("/plan/start")
async def start_plan(
    body: PlanKeyBody,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    from backend.agent.plan_session import start_plan as _start

    return _start(session_id=body.session_id, job_id=body.job_id)


@router.post("/plan/approve")
async def approve_plan(
    body: PlanKeyBody,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    from backend.agent.plan_session import approve_plan as _ap

    try:
        return _ap(session_id=body.session_id, job_id=body.job_id)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/plan/reject")
async def reject_plan(
    body: PlanKeyBody,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    from backend.agent.plan_session import reject_plan as _rj

    return _rj(session_id=body.session_id, job_id=body.job_id)


# ── Headless ──────────────────────────────────────────────────


class HeadlessBody(BaseModel):
    prompt: str
    session_id: Optional[str] = None
    identity_id: Optional[str] = None
    always_approve: bool = False
    max_iterations: Optional[int] = None


@router.post("/headless")
async def headless_run(
    body: HeadlessBody,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    import uuid as _uuid

    from backend.kernel.headless_run import run_headless

    sid = None
    if body.session_id:
        try:
            sid = _uuid.UUID(body.session_id)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"bad session_id: {e}") from e
    return await run_headless(
        body.prompt,
        user_id=current_user.id,
        session_id=sid,
        identity_id=body.identity_id,
        always_approve=body.always_approve,
        max_iterations=body.max_iterations,
    )


# ── Workflow ──────────────────────────────────────────────────


class WorkflowBody(BaseModel):
    workflow: dict[str, Any]
    session_id: Optional[str] = None


@router.post("/workflow")
async def run_workflow(
    body: WorkflowBody,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    import uuid as _uuid

    from backend.kernel.workflow_runner import WorkflowRunner

    if body.session_id:
        try:
            sid: _uuid.UUID | str = _uuid.UUID(body.session_id)
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    else:
        from backend.database import AsyncSessionLocal
        from backend.models.session import Session

        async with AsyncSessionLocal() as db:
            row = Session(
                user_id=current_user.id,
                config={"source": "workflow"},
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            sid = row.id

    runner = WorkflowRunner(
        session_id=sid,
        user_id=current_user.id,
        agent_budget=int((body.workflow or {}).get("agent_budget") or 8),
    )
    return await runner.run(body.workflow or {})


# ── Job rewind ────────────────────────────────────────────────


class RewindBody(BaseModel):
    inbox_item_id: str
    force: bool = True


@router.get("/rewind/{inbox_item_id}")
async def rewind_info(
    inbox_item_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    from backend.agent.job_rewind import job_rewind_info

    info = job_rewind_info(inbox_item_id)
    return {"ok": bool(info), "info": info}


@router.post("/rewind")
async def rewind_job(
    body: RewindBody,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    from backend.agent.job_rewind import rewind_job as _rw

    return _rw(body.inbox_item_id, force=body.force)


# ── Sandbox profiles ──────────────────────────────────────────


class SandboxProfileBody(BaseModel):
    profile: str


@router.get("/sandbox-profiles")
async def sandbox_profiles(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    from backend.computer.profiles import list_profiles, resolve_profile

    cur = resolve_profile()
    return {"profiles": list_profiles(), "current": cur.id}


@router.put("/sandbox-profiles")
async def set_sandbox_profile(
    body: SandboxProfileBody,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    from backend.computer.profiles import resolve_profile
    from backend.core.config import settings

    prof = resolve_profile(body.profile)
    settings.agent_sandbox_profile = prof.id  # type: ignore[attr-defined]
    if prof.force_working_mode:
        settings.agent_working_mode = prof.force_working_mode  # type: ignore[attr-defined]
    settings.agent_computer_network = prof.network  # type: ignore[attr-defined]
    try:
        from backend.repositories.setting_repo import AsyncSettingRepository

        await AsyncSettingRepository().upsert("agent_sandbox_profile", prof.id)
    except Exception:
        pass
    return {"ok": True, "profile": prof.id, "network": prof.network}


# ── Subagent types catalog ────────────────────────────────────


@router.get("/subagent-types")
async def subagent_types(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    from backend.agent.subagent_types import SPECS

    return {
        "types": [
            {
                "kind": s.kind,
                "name": s.name,
                "description": s.description,
                "worktree": s.worktree,
                "chat_mode": s.chat_mode,
            }
            for s in SPECS.values()
        ]
    }


class TypedSubagentBody(BaseModel):
    kind: str = "general"
    goal: str
    context: str = ""
    session_id: Optional[str] = None
    use_worktree: Optional[bool] = None


@router.post("/subagent")
async def run_typed_subagent_api(
    body: TypedSubagentBody,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    import uuid as _uuid

    from backend.agent.subagent_types import run_typed_subagent

    if not body.session_id:
        raise HTTPException(status_code=400, detail="session_id required")
    try:
        sid = _uuid.UUID(body.session_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    text = await run_typed_subagent(
        kind=body.kind,
        goal=body.goal,
        session_id=sid,
        context=body.context,
        user_id=current_user.id,
        use_worktree=body.use_worktree,
    )
    return {"ok": True, "text": text, "kind": body.kind}
