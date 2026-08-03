"""okr_goal — 经营目标树（O-KR，SQLite goals 表）的 Agent 工具。

与 manage_goal 完全不同：
- manage_goal：当前会话内的 Todo/完成卡（聊天过程规划）
- okr_goal：目标页的经营 Objective / Key Result（持久化，可挂责任员工）

CEO / 管家被要求「改目标 / 定目标 / 更新进度」时必须用本工具，
不要 grep 前端源码、不要读仓库外路径。
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from backend.skills.base import BaseSkill


class OkrGoalSkill(BaseSkill):
    name = "okr_goal"
    description = (
        "管理「经营目标」O-KR 树（目标页 /goals，SQLite 持久化）。"
        "用于：列出目标、创建/修改 Objective 或 KR、改标题/说明/进度/状态/责任人。"
        "不要用 manage_goal（那是会话 Todo）。不要用 grep/file_read 在代码里找目标。"
        "改标题请 action=update 并传 goal_id + title。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "get", "create", "update", "delete"],
                "description": "list=树；get=单条；create；update；delete",
            },
            "goal_id": {"type": "string", "description": "get/update/delete 时必填"},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "kind": {
                "type": "string",
                "enum": ["objective", "key_result"],
                "description": "create 时：objective 或 key_result",
            },
            "parent_id": {
                "type": "string",
                "description": "create key_result 时挂到哪个 objective",
            },
            "status": {
                "type": "string",
                "enum": ["active", "achieved", "dropped"],
            },
            "progress": {
                "type": "number",
                "description": "0-100（KR 自报；O 通常由子 KR 均值）",
            },
            "owner_identity_id": {
                "type": "string",
                "description": "责任员工 identity id",
            },
            "due_date": {"type": "string", "description": "ISO 日期 YYYY-MM-DD"},
            "auto_dispatch": {
                "type": "boolean",
                "description": "create 且有 owner 时是否自动派工（默认 false，避免改目标时连环派单）",
            },
        },
        "required": ["action"],
    }

    async def execute(self, **kwargs: Any) -> str:
        action = str(kwargs.get("action") or "").strip().lower()
        try:
            from backend.repositories.goal_repo import AsyncGoalRepository

            repo = AsyncGoalRepository()
            if action == "list":
                return await self._list(repo)
            if action == "get":
                return await self._get(repo, str(kwargs.get("goal_id") or ""))
            if action == "create":
                return await self._create(repo, kwargs)
            if action == "update":
                return await self._update(repo, kwargs)
            if action == "delete":
                return await self._delete(repo, str(kwargs.get("goal_id") or ""))
            return json.dumps(
                {"ok": False, "message": f"unknown action: {action}"},
                ensure_ascii=False,
            )
        except Exception as e:
            return json.dumps(
                {"ok": False, "message": f"okr_goal failed: {e}"},
                ensure_ascii=False,
            )

    async def _list(self, repo: Any) -> str:
        all_goals = await repo.list_all()
        krs_by_parent: dict[str, list] = {}
        objectives = []
        for g in all_goals:
            if g.kind == "objective":
                objectives.append(g)
            elif g.parent_id is not None:
                krs_by_parent.setdefault(str(g.parent_id), []).append(g)
        tree = []
        for o in objectives:
            krs = krs_by_parent.get(str(o.id), [])
            od = o.to_dict()
            if krs:
                od["progress"] = round(sum(k.progress for k in krs) / len(krs), 1)
            od["key_results"] = [k.to_dict() for k in krs]
            tree.append(od)
        return json.dumps(
            {"ok": True, "objectives": tree, "total": len(tree)},
            ensure_ascii=False,
            indent=2,
        )

    async def _get(self, repo: Any, goal_id: str) -> str:
        gid = self._uuid(goal_id)
        if gid is None:
            return json.dumps({"ok": False, "message": "invalid goal_id"}, ensure_ascii=False)
        g = await repo.get_by_id(gid)
        if g is None:
            return json.dumps({"ok": False, "message": "goal not found"}, ensure_ascii=False)
        return json.dumps({"ok": True, "goal": g.to_dict()}, ensure_ascii=False, indent=2)

    async def _create(self, repo: Any, kwargs: dict[str, Any]) -> str:
        title = str(kwargs.get("title") or "").strip()
        if not title:
            return json.dumps({"ok": False, "message": "title required"}, ensure_ascii=False)
        kind = str(kwargs.get("kind") or "objective").strip() or "objective"
        data: dict[str, Any] = {
            "title": title[:200],
            "description": str(kwargs.get("description") or ""),
            "kind": kind,
            "status": str(kwargs.get("status") or "active"),
            "progress": self._clamp_progress(kwargs.get("progress")),
            "owner_identity_id": (str(kwargs.get("owner_identity_id") or "").strip() or None),
            "due_date": (str(kwargs.get("due_date") or "").strip() or None),
        }
        parent_raw = str(kwargs.get("parent_id") or "").strip()
        if parent_raw:
            pid = self._uuid(parent_raw)
            if pid is None:
                return json.dumps({"ok": False, "message": "invalid parent_id"}, ensure_ascii=False)
            data["parent_id"] = pid
        elif kind == "key_result":
            return json.dumps(
                {"ok": False, "message": "key_result 需要 parent_id（所属 objective）"},
                ensure_ascii=False,
            )
        goal = await repo.create(data)
        out: dict[str, Any] = {"ok": True, "goal": goal.to_dict()}
        # 可选派工（默认关：对话里「改目标」不应连环派单）
        auto = kwargs.get("auto_dispatch")
        if auto is True and goal.owner_identity_id:
            try:
                from backend.api.routes.goals import _dispatch_goal_to_owner

                out["dispatch"] = await _dispatch_goal_to_owner(goal)
            except Exception as e:
                out["dispatch"] = {"dispatched": False, "message": str(e)}
        return json.dumps(out, ensure_ascii=False, indent=2)

    async def _update(self, repo: Any, kwargs: dict[str, Any]) -> str:
        gid = self._uuid(str(kwargs.get("goal_id") or ""))
        if gid is None:
            return json.dumps({"ok": False, "message": "goal_id required"}, ensure_ascii=False)
        data: dict[str, Any] = {}
        for key in ("title", "description", "status", "owner_identity_id", "due_date"):
            if kwargs.get(key) is not None and str(kwargs.get(key)).strip() != "":
                val = kwargs.get(key)
                if key == "title":
                    data[key] = str(val)[:200]
                elif key == "owner_identity_id":
                    data[key] = str(val).strip() or None
                else:
                    data[key] = val
        if kwargs.get("progress") is not None:
            data["progress"] = self._clamp_progress(kwargs.get("progress"))
        if not data:
            return json.dumps(
                {"ok": False, "message": "nothing to update（请传 title/description/status/progress 等）"},
                ensure_ascii=False,
            )
        goal = await repo.update(gid, data)
        if goal is None:
            return json.dumps({"ok": False, "message": "goal not found"}, ensure_ascii=False)
        return json.dumps({"ok": True, "goal": goal.to_dict()}, ensure_ascii=False, indent=2)

    async def _delete(self, repo: Any, goal_id: str) -> str:
        gid = self._uuid(goal_id)
        if gid is None:
            return json.dumps({"ok": False, "message": "invalid goal_id"}, ensure_ascii=False)
        ok = await repo.delete(gid)
        return json.dumps({"ok": bool(ok), "deleted": bool(ok), "id": goal_id}, ensure_ascii=False)

    @staticmethod
    def _uuid(raw: str) -> uuid.UUID | None:
        try:
            return uuid.UUID(str(raw).strip())
        except Exception:
            return None

    @staticmethod
    def _clamp_progress(v: Any) -> float:
        try:
            p = float(v if v is not None else 0.0)
        except Exception:
            p = 0.0
        return max(0.0, min(100.0, p))
