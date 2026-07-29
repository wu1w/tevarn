"""Lightweight needsFollowUp / completion gate.

Default policy is soft: only hard-force when empty tools (once) or
fix/build without writes. Multi-category shallow checks become soft
allow + epilogue footer so strong models keep agency.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from backend.agent.task_grounding import evaluate_grounding


@dataclass
class CompletionVerdict:
    ok: bool
    reason: str = ""
    nudge: str = ""


def evaluate_completion(
    user_input: str,
    tools_used: Iterable[str],
    final_text: str = "",
    *,
    max_followups_done: int = 0,
    model_name: str | None = None,
) -> CompletionVerdict:
    """Return whether the turn looks complete enough to idle."""
    tools = [str(t) for t in (tools_used or []) if t]
    text = (user_input or "").strip()
    toolset = set(tools)

    # Dispatch path first only when hard policy enabled (soft skips)
    if toolset & {"crew_steward", "delegate_task", "agent_call"}:
        try:
            from backend.agent.dispatch_grounding import evaluate_dispatcher_session

            ok_d, reason_d, nudge_d = evaluate_dispatcher_session(
                text,
                tools,
                final_text,
                max_followups_done=max_followups_done,
                model_name=model_name,
            )
            if not ok_d:
                return CompletionVerdict(ok=False, reason=reason_d, nudge=nudge_d)
        except Exception:
            pass

    g = evaluate_grounding(
        user_input,
        tools,
        final_text,
        max_followups_done=max_followups_done,
        model_name=model_name,
    )
    if not g.ok:
        return CompletionVerdict(ok=False, reason=g.reason, nudge=g.nudge)

    # only_glob: soft-default allows; balanced/strict may soft-nudge once
    try:
        from backend.agent.grounding_policy import get_policy

        pol = get_policy(model_name)
    except Exception:
        pol = None
    if (
        pol is not None
        and pol.hard_list_only
        and tools
        and set(tools) <= {"glob", "current_time", "use_tool_pack"}
        and len(text) > 40
        and max_followups_done < 1
    ):
        return CompletionVerdict(
            ok=False,
            reason="only_glob",
            nudge=(
                "【轻提示】似乎只列了文件。若任务需要读/改/算/搜，可再进一步；"
                "否则直接给结论即可。"
            ),
        )

    return CompletionVerdict(ok=True, reason=g.reason or "ok")


__all__ = ["CompletionVerdict", "evaluate_completion"]
