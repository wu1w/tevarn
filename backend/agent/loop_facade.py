"""Thin agent-loop façade — stable imports for phases / tools / tests.

The fat conductor (``loop.NexusAgentLoop``) remains the run orchestrator;
policy and pure helpers live in sibling modules. Prefer importing from here
or from the specific module rather than reaching into ``loop.py`` guts.
"""

from __future__ import annotations

from backend.agent.goal_facade import (
    goal_mode_tool_extras,
    is_thrash_exit_reason,
    prepare_goal_runtime,
    resolve_goal_mode,
)
from backend.agent.progress_facade import (
    classify_cargo_error,
    is_cargo_compile_failure,
    should_arm_deliver_mode,
)
from backend.agent.session_lock import get_session_lock, remove_session_lock
from backend.agent.tool_errors import sanitize_tool_error, tool_error_next_step

__all__ = [
    "classify_cargo_error",
    "get_session_lock",
    "goal_mode_tool_extras",
    "is_cargo_compile_failure",
    "is_thrash_exit_reason",
    "prepare_goal_runtime",
    "remove_session_lock",
    "resolve_goal_mode",
    "sanitize_tool_error",
    "should_arm_deliver_mode",
    "tool_error_next_step",
]
