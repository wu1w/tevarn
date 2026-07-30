"""Session / job plan store — plan must be approved before build edits.

Hard gate semantics (Grok-style): while state is planning/plan_ready,
PermissionGate mode=plan denies edit+bash regardless of always-approve.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from backend.agent.plan_gate import PlanGate, PlanState

_lock = threading.RLock()
# key = session_id or job:{inbox_id}
_gates: dict[str, PlanGate] = {}


def _key(session_id: str | None = None, job_id: str | None = None) -> str:
    if job_id:
        return f"job:{job_id}"
    return f"session:{session_id or 'default'}"


def get_gate(*, session_id: str | None = None, job_id: str | None = None) -> PlanGate:
    k = _key(session_id, job_id)
    with _lock:
        g = _gates.get(k)
        if g is None:
            g = PlanGate()
            _gates[k] = g
        return g


def requires_plan_approval(
    *,
    session_id: str | None = None,
    job_id: str | None = None,
    chat_mode: str | None = None,
) -> bool:
    """True when edits/shell must be blocked until plan is approved."""
    mode = (chat_mode or "").strip().lower()
    if mode in ("plan", "ask"):
        g = get_gate(session_id=session_id, job_id=job_id)
        if g.state in (PlanState.IDLE, PlanState.DONE, PlanState.CANCELLED, PlanState.BUILDING, PlanState.VERIFYING):
            # plan mode chat without submitted plan still read-only via PermissionGate mode
            return mode == "plan" and g.state != PlanState.BUILDING
        if g.state in (PlanState.PLANNING, PlanState.PLAN_READY):
            return not g.approved
    g = get_gate(session_id=session_id, job_id=job_id)
    if g.state in (PlanState.PLANNING, PlanState.PLAN_READY) and not g.approved:
        return True
    return False


def start_plan(*, session_id: str | None = None, job_id: str | None = None) -> dict[str, Any]:
    g = get_gate(session_id=session_id, job_id=job_id)
    g.start_planning()
    return g.to_dict()


def submit_plan_markdown(
    md: str,
    *,
    session_id: str | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    g = get_gate(session_id=session_id, job_id=job_id)
    if g.state == PlanState.IDLE:
        g.start_planning()
    plan = PlanGate.parse_plan_markdown(md or "")
    g.submit_plan(plan)
    return g.to_dict()


def approve_plan(*, session_id: str | None = None, job_id: str | None = None) -> dict[str, Any]:
    g = get_gate(session_id=session_id, job_id=job_id)
    g.approve()
    return g.to_dict()


def reject_plan(*, session_id: str | None = None, job_id: str | None = None) -> dict[str, Any]:
    g = get_gate(session_id=session_id, job_id=job_id)
    g.reject()
    return g.to_dict()


def cancel_plan(*, session_id: str | None = None, job_id: str | None = None) -> dict[str, Any]:
    g = get_gate(session_id=session_id, job_id=job_id)
    g.cancel()
    return g.to_dict()


def plan_snapshot(*, session_id: str | None = None, job_id: str | None = None) -> dict[str, Any]:
    g = get_gate(session_id=session_id, job_id=job_id)
    data = g.to_dict()
    data["updated_at"] = time.time()
    return data
