"""
Goal 模式状态：todo 列表 + 完成判定

对齐 Cursor / Claude Code 等 agent 的 goal 工作流：
- 创建目标与 todo
- 执行过程中更新进度
- 循环检测是否全部完成；未完成则继续
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

logger = logging.getLogger(__name__)

TodoStatus = Literal["pending", "in_progress", "done", "cancelled", "blocked"]


@dataclass
class GoalTodo:
    id: str
    content: str
    status: TodoStatus = "pending"
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "status": self.status,
            "note": self.note,
        }


@dataclass
class GoalState:
    session_id: str
    title: str = ""
    description: str = ""
    status: Literal["idle", "active", "completed", "blocked", "cancelled"] = "idle"
    todos: list[GoalTodo] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    completion_summary: str = ""

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        done = sum(1 for t in self.todos if t.status == "done")
        total = len(self.todos)
        return {
            "session_id": self.session_id,
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "todos": [t.to_dict() for t in self.todos],
            "progress": {
                "done": done,
                "total": total,
                "percent": int(done * 100 / total) if total else 0,
            },
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completion_summary": self.completion_summary,
            "is_complete": self.is_complete(),
        }

    def is_complete(self) -> bool:
        if self.status in ("completed", "cancelled"):
            return True
        if not self.todos:
            return self.status == "completed"
        # 全部 done 或 cancelled，且至少一个 done
        active = [t for t in self.todos if t.status not in ("cancelled",)]
        if not active:
            return self.status == "completed"
        return all(t.status == "done" for t in active) and self.status != "blocked"

    def remaining(self) -> list[GoalTodo]:
        return [t for t in self.todos if t.status in ("pending", "in_progress", "blocked")]

    def summary_for_llm(self) -> str:
        lines = [
            f"# Goal: {self.title or '(未命名)'}",
            f"Status: {self.status}",
        ]
        if self.description:
            lines.append(f"Description: {self.description}")
        if not self.todos:
            lines.append("Todos: (empty — 请先用 manage_goal 创建任务列表)")
        else:
            lines.append("Todos:")
            for t in self.todos:
                mark = {
                    "pending": "[ ]",
                    "in_progress": "[~]",
                    "done": "[x]",
                    "cancelled": "[-]",
                    "blocked": "[!]",
                }.get(t.status, "[ ]")
                note = f" — {t.note}" if t.note else ""
                lines.append(f"  {mark} {t.id}: {t.content}{note}")
        rem = self.remaining()
        if rem:
            lines.append(f"Remaining: {len(rem)} item(s) — 完成前不要停止。")
        else:
            lines.append("All actionable todos done. Call manage_goal(action=complete) if goal is achieved.")
        return "\n".join(lines)


# session_id -> GoalState（进程内；与 agent 同生命周期）
_goals: dict[str, GoalState] = {}


def get_goal(session_id: str | uuid.UUID) -> GoalState | None:
    return _goals.get(str(session_id))


def ensure_goal(session_id: str | uuid.UUID, title: str = "", description: str = "") -> GoalState:
    key = str(session_id)
    g = _goals.get(key)
    if g is None:
        g = GoalState(session_id=key, title=title, description=description, status="active")
        _goals[key] = g
    else:
        if title:
            g.title = title
        if description:
            g.description = description
        if g.status == "idle":
            g.status = "active"
        g.touch()
    return g


def clear_goal(session_id: str | uuid.UUID) -> None:
    _goals.pop(str(session_id), None)


def apply_manage_goal(
    session_id: str | uuid.UUID,
    *,
    action: str,
    title: str = "",
    description: str = "",
    todos: list[dict[str, Any]] | None = None,
    todo_id: str = "",
    status: str = "",
    content: str = "",
    note: str = "",
    completion_summary: str = "",
) -> dict[str, Any]:
    """处理 manage_goal 工具调用。"""
    action = (action or "").strip().lower()
    key = str(session_id)

    if action == "create":
        g = ensure_goal(key, title=title or "Goal", description=description)
        g.status = "active"
        g.todos = []
        if todos:
            for raw in todos:
                tid = str(raw.get("id") or f"t{len(g.todos)+1}")
                g.todos.append(
                    GoalTodo(
                        id=tid,
                        content=str(raw.get("content") or raw.get("title") or ""),
                        status=_norm_status(raw.get("status") or "pending"),
                        note=str(raw.get("note") or ""),
                    )
                )
        g.touch()
        return {"ok": True, "message": "Goal created", "goal": g.to_dict()}

    g = get_goal(key)
    if g is None and action != "get":
        g = ensure_goal(key, title=title or "Goal", description=description)

    if action == "get":
        if g is None:
            return {"ok": True, "message": "No active goal", "goal": None}
        return {"ok": True, "goal": g.to_dict(), "summary": g.summary_for_llm()}

    if g is None:
        return {"ok": False, "message": "No goal — call create first"}

    if action == "set_todos":
        g.todos = []
        for i, raw in enumerate(todos or []):
            tid = str(raw.get("id") or f"t{i+1}")
            g.todos.append(
                GoalTodo(
                    id=tid,
                    content=str(raw.get("content") or raw.get("title") or ""),
                    status=_norm_status(raw.get("status") or "pending"),
                    note=str(raw.get("note") or ""),
                )
            )
        g.status = "active"
        g.touch()
        return {"ok": True, "message": f"Set {len(g.todos)} todos", "goal": g.to_dict()}

    if action == "add_todo":
        tid = todo_id or f"t{len(g.todos)+1}"
        g.todos.append(
            GoalTodo(id=tid, content=content or "untitled", status="pending", note=note)
        )
        g.status = "active"
        g.touch()
        return {"ok": True, "message": f"Added todo {tid}", "goal": g.to_dict()}

    if action == "update_todo":
        target = next((t for t in g.todos if t.id == todo_id), None)
        if not target and content:
            # 按内容模糊匹配
            target = next((t for t in g.todos if t.content == content), None)
        if target is None:
            return {"ok": False, "message": f"Todo not found: {todo_id or content}", "goal": g.to_dict()}
        if status:
            target.status = _norm_status(status)
        if content and content != target.content:
            target.content = content
        if note:
            target.note = note
        # 自动：有 in_progress 则 active
        if any(t.status == "in_progress" for t in g.todos):
            g.status = "active"
        if g.is_complete():
            g.status = "completed"
        g.touch()
        return {"ok": True, "message": f"Updated {target.id}", "goal": g.to_dict()}

    if action == "complete":
        if todos:
            # 允许最终同步一次 todos
            for raw in todos:
                tid = str(raw.get("id") or "")
                t = next((x for x in g.todos if x.id == tid), None)
                if t and raw.get("status"):
                    t.status = _norm_status(raw["status"])
        # 未完成的 pending 标为 done（模型明确 complete 时）
        for t in g.todos:
            if t.status in ("pending", "in_progress"):
                t.status = "done"
        g.status = "completed"
        g.completion_summary = completion_summary or note or "Goal completed"
        g.touch()
        return {"ok": True, "message": "Goal marked complete", "goal": g.to_dict()}

    if action == "block":
        g.status = "blocked"
        if note:
            g.completion_summary = note
        g.touch()
        return {"ok": True, "message": "Goal blocked", "goal": g.to_dict()}

    if action == "cancel":
        g.status = "cancelled"
        g.touch()
        return {"ok": True, "message": "Goal cancelled", "goal": g.to_dict()}

    return {
        "ok": False,
        "message": f"Unknown action: {action}. Use create|set_todos|add_todo|update_todo|get|complete|block|cancel",
        "goal": g.to_dict() if g else None,
    }


def _norm_status(s: Any) -> TodoStatus:
    v = str(s or "pending").lower().strip()
    mapping = {
        "pending": "pending",
        "todo": "pending",
        "in_progress": "in_progress",
        "doing": "in_progress",
        "progress": "in_progress",
        "done": "done",
        "completed": "done",
        "complete": "done",
        "cancelled": "cancelled",
        "canceled": "cancelled",
        "blocked": "blocked",
        "fail": "blocked",
        "failed": "blocked",
    }
    return mapping.get(v, "pending")  # type: ignore[return-value]


def dump_goal_json(goal: GoalState | None) -> str:
    if goal is None:
        return json.dumps({"goal": None}, ensure_ascii=False)
    return json.dumps(goal.to_dict(), ensure_ascii=False, indent=2)
