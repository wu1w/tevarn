"""Coding loop state machine: understand → plan → edit → test → review → deliver.

Orthogonal to Durable RunStatus (created/planning/executing/…). This tracks the
*engineering workflow* inside a single agent run so the model and UI share one
honest phase, and soft controller notes can unstick thrash.
"""
from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class CodingPhase(str, Enum):
    IDLE = "idle"
    UNDERSTAND = "understand"
    PLAN = "plan"
    EDIT = "edit"
    TEST = "test"
    REVIEW = "review"
    DELIVER = "deliver"
    DONE = "done"


# Legal forward (and limited back-edge) transitions
_TRANSITIONS: dict[CodingPhase, frozenset[CodingPhase]] = {
    CodingPhase.IDLE: frozenset({CodingPhase.UNDERSTAND, CodingPhase.DONE}),
    CodingPhase.UNDERSTAND: frozenset(
        {
            CodingPhase.PLAN,
            CodingPhase.EDIT,  # small tasks may skip explicit plan
            CodingPhase.DONE,
        }
    ),
    CodingPhase.PLAN: frozenset(
        {
            CodingPhase.EDIT,
            CodingPhase.UNDERSTAND,
            CodingPhase.DONE,
        }
    ),
    CodingPhase.EDIT: frozenset(
        {
            CodingPhase.EDIT,
            CodingPhase.TEST,
            CodingPhase.REVIEW,
            CodingPhase.PLAN,
            CodingPhase.DONE,
        }
    ),
    CodingPhase.TEST: frozenset(
        {
            CodingPhase.EDIT,  # failed tests → fix
            CodingPhase.REVIEW,
            CodingPhase.DELIVER,
            CodingPhase.TEST,
            CodingPhase.DONE,
        }
    ),
    CodingPhase.REVIEW: frozenset(
        {
            CodingPhase.EDIT,
            CodingPhase.TEST,
            CodingPhase.DELIVER,
            CodingPhase.DONE,
        }
    ),
    CodingPhase.DELIVER: frozenset({CodingPhase.DONE, CodingPhase.EDIT}),
    CodingPhase.DONE: frozenset(),
}

_READ_TOOLS = frozenset(
    {
        "file_read",
        "read",
        "grep",
        "search",
        "glob",
        "list_dir",
        "directory_list",
        "semantic_search",
        "codebase_search",
    }
)
_WRITE_TOOLS = frozenset(
    {
        "file_write",
        "edit",
        "apply_patch",
        "desktop_write_file",
        "doc_write",
        "str_replace",
    }
)
_TEST_HINT = re.compile(
    r"\b(pytest|cargo\s+test|npm\s+test|pnpm\s+test|yarn\s+test|"
    r"go\s+test|unittest|jest|vitest|mvn\s+test|make\s+test)\b",
    re.I,
)
_CODING_INTENT = re.compile(
    r"(修复|修改代码|改代码|改一下代码|重写|实现|重构|"
    r"debug|fix\b|bug\b|implement|refactor|code\b|"
    r"函数|源码|模块|api\b|单元测试|compile|编译|报错|traceback|"
    r"patch|pull request|\.(py|ts|tsx|go|rs|java)\b)",
    re.I,
)


@dataclass
class CodingLoopState:
    phase: CodingPhase = CodingPhase.IDLE
    history: list[dict[str, Any]] = field(default_factory=list)
    started_at: float = 0.0
    phase_entered_at: float = 0.0
    files_touched: int = 0
    tests_run: int = 0
    tests_failed: int = 0
    active: bool = False
    last_nudge_phase: str = ""
    iters_in_phase: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase.value,
            "active": self.active,
            "files_touched": self.files_touched,
            "tests_run": self.tests_run,
            "tests_failed": self.tests_failed,
            "iters_in_phase": self.iters_in_phase,
            "history": list(self.history)[-12:],
        }


_lock = threading.Lock()
_states: dict[str, CodingLoopState] = {}


def _key(session_id: uuid.UUID | str) -> str:
    return str(session_id)


def get_coding_loop(session_id: uuid.UUID | str) -> CodingLoopState:
    k = _key(session_id)
    with _lock:
        st = _states.get(k)
        if st is None:
            st = CodingLoopState()
            _states[k] = st
        return st


def drop_coding_loop(session_id: uuid.UUID | str) -> None:
    with _lock:
        _states.pop(_key(session_id), None)


def should_activate_coding_loop(
    user_input: str,
    *,
    mode: str = "default",
    profile: str = "",
) -> bool:
    """Heuristic: coding profile / plan mode / engineering-looking prompt.

    Explicitly skips MCP/config-ops turns so manage_mcp micro-loop is not
    polluted by coding.phase nudges or empty delivery cards.
    """
    mode_l = (mode or "").lower()
    prof = (profile or "").lower()
    text = (user_input or "").strip()
    # MCP / 配置运维：不走工程状态机
    try:
        from backend.agent.tool_policy import is_mcp_ops_intent

        if text and is_mcp_ops_intent(text):
            return False
    except Exception:
        if text and re.search(r"(?i)mcp|model\s*context\s*protocol", text):
            if re.search(r"(配置|安装|接入|密钥|api\s*key|token)", text):
                return False
    if mode_l in ("plan", "goal", "coding"):
        return True
    if prof in ("coding", "engineering"):
        # assistant profile alone is too broad (chat + light tools)
        if prof == "engineering" or (prof == "coding" and mode_l != "default"):
            return True
        if prof == "coding" and text and _CODING_INTENT.search(text):
            return True
    if len(text) < 4:
        return False
    return bool(_CODING_INTENT.search(text))


def start_coding_loop(
    session_id: uuid.UUID | str,
    *,
    goal: str = "",
    force: bool = False,
    user_input: str = "",
    mode: str = "default",
) -> CodingLoopState:
    st = get_coding_loop(session_id)
    if st.active and not force:
        return st
    activate = force or should_activate_coding_loop(user_input or goal, mode=mode)
    if not activate:
        st.active = False
        st.phase = CodingPhase.IDLE
        return st
    now = time.time()
    st.active = True
    st.phase = CodingPhase.UNDERSTAND
    st.started_at = now
    st.phase_entered_at = now
    st.files_touched = 0
    st.tests_run = 0
    st.tests_failed = 0
    st.iters_in_phase = 0
    st.history = [
        {
            "from": CodingPhase.IDLE.value,
            "to": CodingPhase.UNDERSTAND.value,
            "reason": "start",
            "t": now,
        }
    ]
    return st


def _can_transition(src: CodingPhase, dst: CodingPhase) -> bool:
    if src == dst:
        return True
    return dst in _TRANSITIONS.get(src, frozenset())


def transition(
    session_id: uuid.UUID | str,
    dst: CodingPhase | str,
    *,
    reason: str = "",
) -> CodingLoopState:
    st = get_coding_loop(session_id)
    if not st.active:
        return st
    target = CodingPhase(dst) if not isinstance(dst, CodingPhase) else dst
    if not _can_transition(st.phase, target):
        logger.debug(
            "coding_loop illegal %s → %s (%s)",
            st.phase.value,
            target.value,
            reason,
        )
        return st
    if st.phase == target:
        return st
    now = time.time()
    st.history.append(
        {
            "from": st.phase.value,
            "to": target.value,
            "reason": (reason or "")[:80],
            "t": now,
        }
    )
    st.phase = target
    st.phase_entered_at = now
    st.iters_in_phase = 0
    return st


def observe_tool(
    session_id: uuid.UUID | str,
    tool_name: str,
    *,
    arguments: dict[str, Any] | None = None,
    result: str = "",
) -> CodingLoopState:
    """Advance phase from tool evidence (best-effort, never raises)."""
    st = get_coding_loop(session_id)
    if not st.active:
        return st
    name = (tool_name or "").strip().lower()
    args = arguments or {}

    if name in _READ_TOOLS or name in ("search_knowledge_base",):
        # stay in understand/plan; if somehow idle, open understand
        if st.phase == CodingPhase.IDLE:
            transition(session_id, CodingPhase.UNDERSTAND, reason="read")
        return st

    if name in _WRITE_TOOLS:
        st.files_touched += 1
        if st.phase in (CodingPhase.UNDERSTAND, CodingPhase.PLAN, CodingPhase.IDLE):
            transition(session_id, CodingPhase.EDIT, reason=f"write:{name}")
        elif st.phase in (CodingPhase.TEST, CodingPhase.REVIEW):
            # bugfix cycle
            transition(session_id, CodingPhase.EDIT, reason=f"rewrite:{name}")
        return st

    if name in ("command", "shell", "process", "python", "bash"):
        cmd = str(
            args.get("command") or args.get("cmd") or args.get("code") or ""
        )
        if _TEST_HINT.search(cmd):
            st.tests_run += 1
            res_l = (result or "").lower()
            failed = any(
                x in res_l
                for x in ("failed", "traceback", "error", "failures")
            ) and "passed" not in res_l[:400]
            if failed:
                st.tests_failed += 1
            if st.phase in (
                CodingPhase.EDIT,
                CodingPhase.UNDERSTAND,
                CodingPhase.PLAN,
                CodingPhase.REVIEW,
            ):
                transition(session_id, CodingPhase.TEST, reason="test_cmd")
            if not failed and st.phase == CodingPhase.TEST and st.files_touched:
                transition(session_id, CodingPhase.REVIEW, reason="tests_ok")
        return st

    return st


def tick_iteration(session_id: uuid.UUID | str) -> CodingLoopState:
    st = get_coding_loop(session_id)
    if st.active:
        st.iters_in_phase += 1
    return st


def controller_nudge(session_id: uuid.UUID | str) -> str:
    """One-line soft guidance when a phase stalls. Empty if nothing to say."""
    st = get_coding_loop(session_id)
    if not st.active:
        return ""
    phase = st.phase
    n = st.iters_in_phase
    # avoid repeating the same nudge every iter
    key = f"{phase.value}:{n // 3}"
    if st.last_nudge_phase == key:
        return ""

    msg = ""
    if phase == CodingPhase.UNDERSTAND and n >= 4:
        msg = (
            "[Coding loop] Still in understand — if the task is clear, "
            "outline a short plan or start the first edit."
        )
    elif phase == CodingPhase.PLAN and n >= 4:
        msg = (
            "[Coding loop] Plan is enough — move to concrete file edits."
        )
    elif phase == CodingPhase.EDIT and n >= 5 and st.files_touched and st.tests_run == 0:
        msg = (
            "[Coding loop] Edits landed with no tests yet — run a focused test "
            "or say why tests are skipped."
        )
    elif phase == CodingPhase.TEST and st.tests_failed and n >= 2:
        msg = (
            "[Coding loop] Tests failed — fix the failing case, then re-test."
        )
    elif phase == CodingPhase.TEST and not st.tests_failed and n >= 3:
        msg = (
            "[Coding loop] Tests look OK — briefly review diffs and deliver."
        )
    elif phase == CodingPhase.REVIEW and n >= 3:
        msg = (
            "[Coding loop] Review done — summarize delivery (files, tests, next)."
        )

    if msg:
        st.last_nudge_phase = key
    return msg


def mark_deliver(session_id: uuid.UUID | str) -> CodingLoopState:
    st = get_coding_loop(session_id)
    if not st.active:
        return st
    if st.phase != CodingPhase.DONE:
        # allow jump to deliver from most phases at epilogue
        if st.phase != CodingPhase.DELIVER:
            prev = st.phase
            st.history.append(
                {
                    "from": prev.value,
                    "to": CodingPhase.DELIVER.value,
                    "reason": "epilogue",
                    "t": time.time(),
                }
            )
            st.phase = CodingPhase.DELIVER
            st.phase_entered_at = time.time()
        transition(session_id, CodingPhase.DONE, reason="epilogue_done")
    return st


def phase_label(phase: CodingPhase | str) -> str:
    p = phase.value if isinstance(phase, CodingPhase) else str(phase)
    labels = {
        "idle": "Idle",
        "understand": "Understand",
        "plan": "Plan",
        "edit": "Edit",
        "test": "Test",
        "review": "Review",
        "deliver": "Deliver",
        "done": "Done",
    }
    return labels.get(p, p)
