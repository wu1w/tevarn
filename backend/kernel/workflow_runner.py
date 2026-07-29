"""Lightweight JSON workflow runner: sequential + parallel agent steps with budget.

Schema:
{
  "name": "audit",
  "agent_budget": 8,
  "steps": [
    {"id": "s1", "type": "agent", "role": "explore", "goal": "..."},
    {"id": "s2", "type": "parallel", "items": [
       {"role": "implement", "goal": "..."},
       {"role": "review", "goal": "..."}
    ]}
  ]
}
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)


class WorkflowBudgetExceeded(RuntimeError):
    pass


class WorkflowRunner:
    def __init__(
        self,
        *,
        session_id: uuid.UUID | str,
        user_id: uuid.UUID | None = None,
        ws_manager: Any = None,
        agent_budget: int = 8,
    ) -> None:
        self.session_id = session_id
        self.user_id = user_id
        self.ws_manager = ws_manager
        self.agent_budget = max(1, int(agent_budget or 8))
        self.used = 0
        self.results: list[dict[str, Any]] = []

    def _consume(self, n: int = 1) -> None:
        if self.used + n > self.agent_budget:
            raise WorkflowBudgetExceeded(
                f"agent budget exceeded ({self.used}+{n}>{self.agent_budget})"
            )
        self.used += n

    async def _run_agent(self, role: str, goal: str, context: str = "") -> str:
        from backend.agent.subagent_types import run_typed_subagent

        self._consume(1)
        return await run_typed_subagent(
            kind=role or "general",
            goal=goal,
            session_id=self.session_id,
            context=context,
            user_id=self.user_id,
            ws_manager=self.ws_manager,
        )

    async def run(self, workflow: dict[str, Any]) -> dict[str, Any]:
        name = str(workflow.get("name") or "workflow")
        if workflow.get("agent_budget") is not None:
            self.agent_budget = max(1, int(workflow["agent_budget"]))
        steps = workflow.get("steps") or []
        if not isinstance(steps, list):
            return {"ok": False, "error": "steps must be a list", "name": name}

        for step in steps:
            if not isinstance(step, dict):
                continue
            stype = str(step.get("type") or "agent").lower()
            sid = str(step.get("id") or f"step-{len(self.results)}")
            try:
                if stype == "parallel":
                    items = step.get("items") or step.get("agents") or []
                    coros = []
                    for it in items:
                        if not isinstance(it, dict):
                            continue
                        role = str(it.get("role") or it.get("kind") or "general")
                        goal = str(it.get("goal") or it.get("prompt") or "")
                        if not goal:
                            continue
                        coros.append(self._run_agent(role, goal, str(it.get("context") or "")))
                    # budget pre-check for all
                    if self.used + len(coros) > self.agent_budget:
                        raise WorkflowBudgetExceeded(
                            f"parallel would exceed budget ({self.used}+{len(coros)}>{self.agent_budget})"
                        )
                    # _run_agent consumes; pre-adjust by running sequentially count then gather
                    # Actually each _run_agent consumes — gather is fine if we check first
                    outs = await asyncio.gather(*coros, return_exceptions=True)
                    texts = []
                    for o in outs:
                        if isinstance(o, Exception):
                            texts.append(f"[error] {o}")
                        else:
                            texts.append(str(o))
                    self.results.append({"id": sid, "type": "parallel", "outputs": texts})
                else:
                    role = str(step.get("role") or step.get("kind") or "general")
                    goal = str(step.get("goal") or step.get("prompt") or "")
                    if not goal:
                        self.results.append({"id": sid, "type": "agent", "error": "empty goal"})
                        continue
                    text = await self._run_agent(role, goal, str(step.get("context") or ""))
                    self.results.append({"id": sid, "type": "agent", "role": role, "output": text})
            except WorkflowBudgetExceeded as e:
                self.results.append({"id": sid, "error": str(e)})
                return {
                    "ok": False,
                    "name": name,
                    "error": str(e),
                    "agent_used": self.used,
                    "agent_budget": self.agent_budget,
                    "steps": self.results,
                }

        return {
            "ok": True,
            "name": name,
            "agent_used": self.used,
            "agent_budget": self.agent_budget,
            "steps": self.results,
        }
