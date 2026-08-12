"""Thin Goal facade for the agent loop.

Hermes-style: goal promote / inject / max-iter live *outside* the fat
conductor. ``goal_state`` remains the store; this module is the only
surface ``loop.py`` should call for goal runtime wiring.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# User messages that mean "continue the existing goal", not a fresh Q&A turn.
# Keep aligned with backend.agent.robust.is_continue_phrase (shared continue surface).
_CONTINUE_GOAL_RE = re.compile(
    r"(?i)^\s*("
    r"请继续|继续|接着做|接着干|往下做|继续推进|继续目标|"
    r"continue|resume|keep\s+going|go\s+on|"
    r"【系统自动续跑】|\[System auto-resume\]"
    r")",
)


def looks_like_goal_continue(user_input: str | None) -> bool:
    """True when the user is continuing an existing goal, not a new question."""
    t = (user_input or "").strip()
    if not t:
        return False
    if t.startswith("【系统自动续跑】") or t.startswith("[System auto-resume]"):
        return True
    # Shared continue surface (includes 「那你接着下一项工作」)
    try:
        from backend.agent.robust import is_continue_phrase

        if is_continue_phrase(t):
            return True
    except Exception:
        pass
    # Short continue phrases (with optional punctuation)
    if _CONTINUE_GOAL_RE.match(t):
        return True
    if len(t) <= 24 and re.search(
        r"(?i)^(请继续|继续|接着|continue|resume)\b", t
    ):
        return True
    return False


def looks_like_casual_or_read_only(user_input: str | None) -> bool:
    """Heuristic: docs/read/summarize Q&A should not enter goal mode."""
    t = (user_input or "").strip()
    if not t or looks_like_goal_continue(t):
        return False
    # Explicit goal management
    if re.search(r"(?i)(目标模式|开启目标|manage_goal|set_todos|拆成目标)", t):
        return False
    # Read / explain / plan questions — no goal machinery
    if re.search(
        r"(?i)("
        r"读(一下|读|下)?文档|看(看|下)文档|总结|阶段|下一步应该|"
        r"按照文档|按文档|什么意思|怎么设计|解释一下|讲讲|"
        r"read\s+(the\s+)?docs?|summar(y|ize)|what\s+next|"
        r"how\s+does|explain|overview|现状|进度如何"
        r")",
        t,
    ):
        return True
    # Short pure questions / greetings / one-shot search
    if len(t) <= 80 and t.endswith(("？", "?")):
        return True
    if len(t) <= 40 and re.search(
        r"(?i)^(你好|嗨|在吗|hello|hi|thanks|谢谢|好的)[\s!！。.~～]*$", t
    ):
        return True
    if re.search(
        r"(?i)(搜\s*一下|搜索\s*一下|帮我\s*搜|查一下新闻|search\s+for)", t
    ) and not re.search(r"(?i)(目标|todo|拆解|分步|plan\s*mode)", t):
        return True
    return False


async def resolve_goal_mode(
    session_id: Any,
    mode: str,
    *,
    user_input: str | None = None,
    origin: str | None = None,
) -> bool:
    """True when this turn should run as goal mode.

    - Explicit ``mode=goal`` always wins.
    - Auto-resume / 「请继续」 + active incomplete goal → promote.
    - Casual read/summarize Q&A → **never** promote (even if a goal exists).
    - Completed goals never promote.
    """
    mode_l = str(mode or "").lower()
    if mode_l == "goal":
        return True

    # Auto-resume origin always continues incomplete goals
    origin_l = str(origin or "").lower()
    is_autoresume = origin_l in (
        "auto_resume",
        "autoresume",
        "goal_resume",
        "segment_resume",
        "resume",
    ) or looks_like_goal_continue(user_input)

    if looks_like_casual_or_read_only(user_input) and not is_autoresume:
        logger.info(
            "skip goal promote (casual/read-only turn) session=%s",
            session_id,
        )
        return False

    try:
        from backend.agent.goal_state import get_goal, load_goal_from_db

        await load_goal_from_db(session_id)
        g = get_goal(session_id)
        if g is None:
            return False
        if g.is_complete() or str(getattr(g, "status", "") or "") in (
            "completed",
            "cancelled",
            "idle",
        ):
            return False
        if str(getattr(g, "status", "") or "") != "active":
            return False
        # Incomplete active goal: only promote on continue/autoresume, not every chat
        if not is_autoresume:
            logger.info(
                "skip goal promote (active goal but not continue phrase) session=%s",
                session_id,
            )
            return False
        logger.info(
            "promote mode=%s→goal (active incomplete goal + continue) session=%s",
            mode,
            session_id,
        )
        return True
    except Exception as e:
        logger.debug("goal_mode promote skip: %s", e)
    return False


async def prepare_goal_runtime(
    *,
    session_id: Any,
    messages: list[dict[str, Any]],
    enriched_input: str,
    max_iterations: int,
    push_goal_update: Any | None = None,
) -> int:
    """Ensure goal, inject LLM summary, return raised max_iterations.

    Does not mutate control flow beyond appending one system message and
    optionally pushing a WS goal update. Caller assigns returned int to
    ``self.max_iterations``.
    """
    from backend.agent.goal_state import (
        ensure_goal,
        get_goal,
        load_goal_from_db,
    )
    from backend.core.config import settings

    goal_iters = int(getattr(settings, "agent_goal_max_iterations", 100) or 100)
    raised = max(int(max_iterations or 0), goal_iters)
    await load_goal_from_db(session_id)
    # Only create/refresh title when no goal yet; do not overwrite with every
    # casual user message (that made every chat look like a new goal).
    g_exist = get_goal(session_id)
    if g_exist is None:
        ensure_goal(
            session_id,
            title=(enriched_input or "")[:120],
            description=(enriched_input or "")[:2000],
        )
    elif str(getattr(g_exist, "status", "") or "") == "idle":
        ensure_goal(session_id, title=(enriched_input or "")[:120])
    if push_goal_update is not None:
        try:
            await push_goal_update(session_id)
        except Exception as e:
            logger.debug("push_goal_update skip: %s", e)

    g0 = get_goal(session_id)
    if g0 and not g0.is_complete():
        anchor = ""
        try:
            from backend.agent.progress_guard import resume_anchor_block
            from backend.tools.permissions import resolve_agent_workspace_root

            anchor = resume_anchor_block(
                resolve_agent_workspace_root() or "",
                goal_active=True,
            )
        except Exception:
            anchor = ""
        messages.append(
            {
                "role": "system",
                "content": (
                    (anchor + "\n" if anchor else "")
                    + "Goal runtime status (update via manage_goal when todos change):\n"
                    + g0.summary_for_llm()
                    + "\nDo **not** start every turn with manage_goal(get) — only when "
                    "progress actually changed or you need the list. Prefer real work "
                    "(read/edit/check) first. Reply to the user in their language."
                ),
            }
        )
    return raised


def goal_mode_tool_extras() -> list[str]:
    """Extra tools always visible in goal mode (policy layer)."""
    return ["manage_goal", "autopilot", "okr_goal"]


# Shared predicate for segment auto-continue + epilogue auto-resume skip.
# Keep in sync with loop.py segment boundary (single source of truth).
THRASH_EXIT_REASONS: frozenset[str] = frozenset(
    {
        "doom_loop",
        "thrash",
        "tool_thrash",
        "alternate_thrash",
        "empty_content_thrash",
        "llm_stream_error",
        "rust_diag",
        "same_tool_fail",
    }
)


def is_thrash_exit_reason(exit_reason: str | None) -> bool:
    """True when auto-resume / segment continue should be suppressed."""
    ex = str(exit_reason or "").strip().lower()
    return ex in THRASH_EXIT_REASONS
