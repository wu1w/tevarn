"""OpenCode-style message parts for timeline UI."""

from __future__ import annotations

import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

PartType = Literal[
    "text",
    "reasoning",
    "tool",
    "step-start",
    "step-finish",
    "queue",
    "system",
    "diff",
    "error",
]


class Part(BaseModel):
    id: str = Field(default_factory=lambda: f"prt_{uuid.uuid4().hex[:16]}")
    type: PartType
    time_created: float = Field(default_factory=time.time)
    # text / reasoning
    text: str | None = None
    # tool
    tool: str | None = None
    call_id: str | None = None
    state: dict[str, Any] | None = None  # status, input, output
    # step-finish
    tokens: dict[str, Any] | None = None
    cost: float | None = None
    reason: str | None = None
    # misc
    meta: dict[str, Any] = Field(default_factory=dict)

    def to_event(self) -> dict[str, Any]:
        return {"type": "part", "part": self.model_dump()}


def part_text(text: str, *, role_hint: str | None = None) -> Part:
    return Part(type="text", text=text, meta={"role": role_hint} if role_hint else {})


def part_reasoning(text: str) -> Part:
    return Part(type="reasoning", text=text)


def part_tool_start(name: str, call_id: str, arguments: Any) -> Part:
    return Part(
        type="tool",
        tool=name,
        call_id=call_id,
        state={"status": "running", "input": arguments},
    )


def part_tool_end(name: str, call_id: str, output: str, *, ok: bool = True) -> Part:
    return Part(
        type="tool",
        tool=name,
        call_id=call_id,
        state={
            "status": "completed" if ok else "error",
            "output": output[:20000],
        },
    )


def part_step_start(n: int) -> Part:
    return Part(type="step-start", meta={"n": n})


def part_step_finish(n: int, *, tokens: dict[str, Any] | None = None, reason: str = "stop") -> Part:
    return Part(type="step-finish", tokens=tokens, reason=reason, meta={"n": n})
