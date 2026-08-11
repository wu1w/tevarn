"""Structured loop decisions — replace prompt-spam guards with one short action.

GPT-audit P0: models should see a compact next_action, not a stack of system nudges.
Callers map thrash / same-tool failure / timeout into LoopDecision and optionally
append a one-line controller note.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Decision = Literal["allow", "ask", "retry", "redirect", "force_final"]


@dataclass(frozen=True)
class LoopDecision:
    decision: Decision
    reason: str = ""
    next_action: str = ""

    def as_controller_note(self) -> str:
        """At most one short line for the model context."""
        if self.decision == "allow":
            return ""
        action = (self.next_action or "").strip()
        reason = (self.reason or "").strip()
        if self.decision == "force_final":
            return action or "Stop using tools and answer the user now."
        if self.decision == "redirect":
            return action or (f"Change approach ({reason})." if reason else "Change approach.")
        if self.decision == "retry":
            return action or (f"Retry carefully ({reason})." if reason else "Retry once.")
        if self.decision == "ask":
            return action or "Ask the user a concise clarifying question."
        return action


def force_final(reason: str = "budget") -> LoopDecision:
    return LoopDecision(
        decision="force_final",
        reason=reason,
        next_action="Stop tools; give the user a clear final answer.",
    )


def same_tool_failure(tool: str) -> LoopDecision:
    return LoopDecision(
        decision="redirect",
        reason="same_tool_failure",
        next_action=f"Do not retry `{tool}` the same way; change tool or finalize.",
    )


def thrash(reason: str = "thrash") -> LoopDecision:
    return LoopDecision(
        decision="force_final",
        reason=reason,
        next_action="You are looping. Summarize progress and stop.",
    )
