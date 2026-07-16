"""
manage_goal — Goal 模式下的 todo / 完成状态管理
"""

from __future__ import annotations

import json
from typing import Any

from backend.agent.goal_state import apply_manage_goal
from backend.skills.base import BaseSkill


class ManageGoalSkill(BaseSkill):
    name = "manage_goal"
    description = (
        "管理当前复杂任务的 Goal 与 Todo 列表。"
        "复杂多步任务时：先 create/set_todos 规划，执行过程中 update_todo 标记进度，"
        "全部完成后 complete。未完成前不要假装结束。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "create",
                    "set_todos",
                    "add_todo",
                    "update_todo",
                    "get",
                    "complete",
                    "block",
                    "cancel",
                ],
                "description": "操作类型",
            },
            "title": {"type": "string", "description": "目标标题（create 时）"},
            "description": {"type": "string", "description": "目标说明"},
            "todos": {
                "type": "array",
                "description": "todo 列表 [{id, content, status}]",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "content": {"type": "string"},
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "done", "cancelled", "blocked"],
                        },
                        "note": {"type": "string"},
                    },
                },
            },
            "todo_id": {"type": "string", "description": "update_todo 时的 id"},
            "status": {
                "type": "string",
                "enum": ["pending", "in_progress", "done", "cancelled", "blocked"],
                "description": "update_todo 的新状态",
            },
            "content": {"type": "string", "description": "todo 文本"},
            "note": {"type": "string"},
            "completion_summary": {"type": "string", "description": "complete 时的总结"},
            "session_id": {
                "type": "string",
                "description": "可选；通常由系统注入",
            },
        },
        "required": ["action"],
    }

    async def execute(self, **kwargs: Any) -> str:
        session_id = kwargs.pop("session_id", None) or kwargs.pop("user_id", None)
        # user_id 被 loop 注入时不能当 session；loop 会额外传 _session_id
        session_id = kwargs.pop("_session_id", None) or session_id
        if not session_id:
            return json.dumps(
                {"ok": False, "message": "session_id missing"}, ensure_ascii=False
            )

        result = apply_manage_goal(
            session_id,
            action=str(kwargs.get("action") or ""),
            title=str(kwargs.get("title") or ""),
            description=str(kwargs.get("description") or ""),
            todos=kwargs.get("todos"),
            todo_id=str(kwargs.get("todo_id") or ""),
            status=str(kwargs.get("status") or ""),
            content=str(kwargs.get("content") or ""),
            note=str(kwargs.get("note") or ""),
            completion_summary=str(kwargs.get("completion_summary") or ""),
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
