"""Per-run brief: goal / files / tests / blockers for coding delivery + recovery.

GPT-audit P1/P2: one structured object instead of scattering progress across
memory, goal, and free-form tool logs.
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RunBrief:
    goal: str = ""
    constraints: list[str] = field(default_factory=list)
    plan: list[str] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)
    changed_files: list[dict[str, str]] = field(default_factory=list)
    tests: list[dict[str, Any]] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    next_action: str = ""
    checkpoints: list[str] = field(default_factory=list)
    phase: str = ""

    def note_file_change(
        self,
        path: str,
        *,
        action: str = "edit",
        checkpoint: str | None = None,
        checkpoint_id: str | None = None,
        backend: str | None = None,
    ) -> None:
        p = (path or "").strip()
        if not p:
            return
        # de-dupe by path, keep last action
        self.changed_files = [c for c in self.changed_files if c.get("path") != p]
        entry: dict[str, str] = {"path": p, "action": action}
        # python path snapshot
        if checkpoint:
            entry["checkpoint"] = checkpoint
            if checkpoint not in self.checkpoints:
                self.checkpoints.append(checkpoint)
        # rust kernel checkpoint id
        if checkpoint_id:
            entry["checkpoint_id"] = str(checkpoint_id)
            tag = f"rust:{checkpoint_id}"
            if tag not in self.checkpoints:
                self.checkpoints.append(tag)
        if backend:
            entry["backend"] = backend
        elif checkpoint_id:
            entry["backend"] = "rust"
        elif checkpoint:
            entry["backend"] = "python"
        self.changed_files.append(entry)

    def note_test(
        self,
        command: str,
        *,
        passed: bool | None = None,
        summary: str = "",
    ) -> None:
        self.tests.append(
            {
                "command": (command or "")[:200],
                "passed": passed,
                "summary": (summary or "")[:300],
            }
        )

    def note_blocker(self, text: str) -> None:
        t = (text or "").strip()
        if t and t not in self.blockers:
            self.blockers.append(t[:300])

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "constraints": list(self.constraints),
            "plan": list(self.plan),
            "completed": list(self.completed),
            "changed_files": list(self.changed_files),
            "tests": list(self.tests),
            "blockers": list(self.blockers),
            "next_action": self.next_action,
            "checkpoints": list(self.checkpoints)[-10:],
            "phase": self.phase or "",
        }

    def delivery_payload(self) -> dict[str, Any] | None:
        """Payload for UI coding card; None if empty."""
        if not self.changed_files and not self.tests and not self.blockers:
            return None
        return {
            "changed_files": self.changed_files[-30:],
            "tests": self.tests[-10:],
            "blockers": self.blockers[-8:],
            "checkpoints": self.checkpoints[-5:],
            "next_action": self.next_action,
            "goal": (self.goal or "")[:200],
        }


_lock = threading.Lock()
_briefs: dict[str, RunBrief] = {}


def get_brief(session_id: uuid.UUID | str) -> RunBrief:
    k = str(session_id)
    with _lock:
        b = _briefs.get(k)
        if b is None:
            b = RunBrief()
            _briefs[k] = b
        return b


def reset_brief(session_id: uuid.UUID | str, *, goal: str = "") -> RunBrief:
    k = str(session_id)
    with _lock:
        b = RunBrief(goal=(goal or "")[:500])
        _briefs[k] = b
        return b


def drop_brief(session_id: uuid.UUID | str) -> None:
    with _lock:
        _briefs.pop(str(session_id), None)
