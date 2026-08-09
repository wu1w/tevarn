"""Plan gate: plan → approve → build state machine."""

from __future__ import annotations

import re
import time
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class PlanState(str, Enum):
    IDLE = "idle"
    PLANNING = "planning"
    PLAN_READY = "plan_ready"
    BUILDING = "building"
    VERIFYING = "verifying"
    DONE = "done"
    CANCELLED = "cancelled"


class PlanStep(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    title: str
    detail: str = ""
    files: list[str] = Field(default_factory=list)
    status: str = "pending"  # pending|in_progress|done|skipped


class PlanDocument(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    title: str = "Implementation Plan"
    summary: str = ""
    steps: list[PlanStep] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    test_plan: str = ""
    created_at: float = Field(default_factory=time.time)
    raw_markdown: str = ""


class PlanGate:
    def __init__(self) -> None:
        self.state = PlanState.IDLE
        self.plan: PlanDocument | None = None
        self.approved: bool = False
        self.history: list[dict[str, Any]] = []

    def start_planning(self) -> None:
        self.state = PlanState.PLANNING
        self.approved = False
        self.plan = None
        self.history.append({"event": "start_planning", "t": time.time()})

    def submit_plan(self, plan: PlanDocument) -> PlanDocument:
        self.plan = plan
        self.state = PlanState.PLAN_READY
        self.approved = False
        self.history.append({"event": "plan_ready", "id": plan.id, "t": time.time()})
        return plan

    def approve(self) -> None:
        if self.state != PlanState.PLAN_READY or not self.plan:
            raise RuntimeError("no plan ready to approve")
        self.approved = True
        self.state = PlanState.BUILDING
        self.history.append({"event": "approved", "t": time.time()})

    def reject(self) -> None:
        self.approved = False
        self.state = PlanState.PLANNING
        self.history.append({"event": "rejected", "t": time.time()})

    def cancel(self) -> None:
        self.state = PlanState.CANCELLED
        self.approved = False
        self.history.append({"event": "cancelled", "t": time.time()})

    def mark_verifying(self) -> None:
        self.state = PlanState.VERIFYING

    def mark_done(self) -> None:
        self.state = PlanState.DONE

    def reset(self) -> None:
        self.state = PlanState.IDLE
        self.plan = None
        self.approved = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "approved": self.approved,
            "plan": self.plan.model_dump() if self.plan else None,
        }

    @staticmethod
    def parse_plan_markdown(md: str) -> PlanDocument:
        """Best-effort parse of model-produced plan markdown."""
        title = "Implementation Plan"
        m = re.search(r"^#\s+(.+)$", md, re.M)
        if m:
            title = m.group(1).strip()

        summary = ""
        sm = re.search(r"(?:summary|概述|摘要)\s*[:：]\s*(.+)", md, re.I)
        if sm:
            summary = sm.group(1).strip()

        steps: list[PlanStep] = []
        # numbered steps
        for m in re.finditer(r"^\s*(?:\d+[\.\)]\s+|[-*]\s+\[[ xX]?\]\s*|###\s*Step\s*\d+[:：]?\s*)(.+)$", md, re.M):
            line = m.group(1).strip()
            if len(line) < 2:
                continue
            files = re.findall(r"`([^`]+)`", line)
            steps.append(PlanStep(title=re.sub(r"`([^`]+)`", r"\1", line)[:200], files=files[:10]))

        if not steps:
            # fallback: bullet lines under Implementation
            for m in re.finditer(r"^[-*]\s+(.+)$", md, re.M):
                line = m.group(1).strip()
                if len(line) > 8:
                    steps.append(PlanStep(title=line[:200]))
                    if len(steps) >= 12:
                        break

        if not steps:
            steps = [
                PlanStep(title="Analyze codebase and locate change points"),
                PlanStep(title="Implement minimal code changes"),
                PlanStep(title="Run tests and fix failures"),
            ]

        risks = re.findall(r"(?:risk|风险)\s*[:：]\s*(.+)", md, re.I)
        test_plan = ""
        tm = re.search(r"(?:test plan|测试计划|验证)\s*[:：]\s*(.+)", md, re.I)
        if tm:
            test_plan = tm.group(1).strip()

        return PlanDocument(
            title=title,
            summary=summary or md[:280].replace("\n", " "),
            steps=steps[:20],
            risks=risks[:10],
            test_plan=test_plan,
            raw_markdown=md,
        )


def should_auto_plan(user_text: str, *, auto_plan_complex: bool, simple_max_chars: int) -> bool:
    if not auto_plan_complex:
        return False
    t = user_text.strip()
    if not t:
        return False
    # explicit overrides
    low = t.lower()
    if low.startswith("/build") or low.startswith("/ask"):
        return False
    if low.startswith("/plan"):
        return True

    complex_markers = [
        "refactor",
        "重构",
        "migrate",
        "迁移",
        "architecture",
        "架构",
        "implement",
        "实现",
        "add feature",
        "新功能",
        "multi",
        "多个",
        "across",
        "整个",
        "fix flaky",
        "设计",
        "module",
    ]
    if any(k in low for k in complex_markers):
        return True
    if len(t) > simple_max_chars:
        return True
    # multi-sentence / multi-requirement
    if t.count("\n") >= 2 or t.count("。") + t.count(".") >= 3:
        return True
    return False
