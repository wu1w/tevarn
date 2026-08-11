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

    def as_system_message(self) -> dict[str, str] | None:
        note = self.as_controller_note()
        if not note:
            return None
        return {"role": "system", "content": f"[Controller] {note}"}


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


def security_blocked() -> LoopDecision:
    return LoopDecision(
        decision="redirect",
        reason="security_blocked",
        next_action="Prefer file_write/edit/apply_patch; do not retry the blocked command.",
    )


def command_not_found() -> LoopDecision:
    return LoopDecision(
        decision="redirect",
        reason="exit_127",
        next_action="Command not found — fix cwd or use full path; do not repeat the same command.",
    )


def soft_orch_window(code: str = "orch_window_thrash") -> LoopDecision:
    return LoopDecision(
        decision="redirect",
        reason=code,
        next_action="Heavy orchestration detected — digest existing jobs or do real work; tools still allowed.",
    )


def from_guard_code(code: str, reason: str = "") -> LoopDecision:
    """Map loop_guard codes to decisions."""
    c = (code or "").strip().lower()
    if "thrash" in c or "budget" in c or "max_turn" in c:
        return force_final(c or reason or "guard")
    if "window" in c:
        return soft_orch_window(c)
    return LoopDecision(
        decision="force_final",
        reason=c or reason,
        next_action="Write final answer only; tools blocked this round.",
    )
