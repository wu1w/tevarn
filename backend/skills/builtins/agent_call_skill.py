"""
Agent Call Skill - 调用其他 Agent
接入 run_subagent 真·迷你 Run（与 delegate_task 同源）。

保留递归防护：禁止调用自身 / 禁止调用环 / 限制最大调用深度。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from ..base import BaseSkill

logger = logging.getLogger(__name__)

MAX_CALL_DEPTH = 3


class AgentCallSkill(BaseSkill):
    """Agent 调用 Skill"""

    name = "agent_call"
    description = (
        "把任务交给编制中的员工（收件箱工单）。"
        "agent=员工姓名，task=工单内容。"
        "不要用来起临时子代理闷跑；招人请用 crew_steward.hire。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "agent": {
                "type": "string",
                "description": "目标员工姓名或 Identity id",
            },
            "task": {
                "type": "string",
                "description": "要分配的任务/工单描述",
            },
            "context": {
                "type": "string",
                "description": "相关上下文信息",
                "default": "",
            },
        },
        "required": ["agent", "task"],
    }

    async def execute(
        self,
        agent: str,
        task: str,
        context: str = "",
        _caller_agent: str | None = None,
        _call_chain: list[str] | None = None,
        **kwargs: Any,
    ) -> str:
        """编制派活：写入员工 Inbox（不再起 subagent 闷跑）。"""
        target = (agent or "").strip()
        goal = (task or "").strip()
        if not target or not goal:
            return "[Error] agent 与 task 均为必填"

        if _caller_agent and target == _caller_agent:
            return f"[Error] 「{target}」不能把活派给自己。"

        from backend.agent.workforce_dispatch import assign_to_employee

        instruction = goal if not (context or "").strip() else f"{goal}\n\n上下文：{context.strip()}"
        steward_sid = str(kwargs.get("_session_id") or "").strip() or None
        return await assign_to_employee(
            target,
            instruction,
            priority=5,
            via="agent_call",
            steward_session_id=steward_sid,
        )
