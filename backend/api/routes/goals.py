"""Goals 路由 - O-KR 目标树 CRUD

GET    /goals/tree        目标树（O + 各自 KR + O 进度=KR 均值）
POST   /goals             创建（objective 或 key_result）
                          · 指定 owner 时自动向该员工 inbox 派一单（best-effort）
PUT    /goals/{id}        更新（title/description/status/progress/owner/due_date）
DELETE /goals/{id}        删除（objective 级联删除 KR）
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from backend.repositories.goal_repo import AsyncGoalRepository
from backend.schemas.user import UserRead

from ..dependencies import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/goals", tags=["goals"])


class GoalCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    kind: str = "objective"  # objective / key_result
    parent_id: str | None = None
    owner_identity_id: str | None = None
    due_date: str | None = None
    progress: float = 0.0
    # 默认：有责任人就派工；无责任人时可由前端显式 false 跳过
    auto_dispatch: bool = True


class GoalUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None
    progress: float | None = None
    owner_identity_id: str | None = None
    due_date: str | None = None


def _goal_instruction(goal: Any) -> str:
    """把 O/KR 收成 dispatcher 可执行的工单文案。"""
    kind = str(getattr(goal, "kind", "") or "objective")
    label = "关键结果 KR" if kind == "key_result" else "经营目标"
    title = (getattr(goal, "title", None) or "").strip() or "(无标题)"
    desc = (getattr(goal, "description", None) or "").strip()
    due = (getattr(goal, "due_date", None) or "").strip()
    gid = str(getattr(goal, "id", "") or "")
    lines = [
        f"【{label}工单 · 目标系统自动派发】",
        f"标题：{title}",
    ]
    if desc:
        lines.append(f"说明：{desc}")
    if due:
        lines.append(f"截止日期：{due}")
    if gid:
        lines.append(f"目标 ID：{gid}")
    lines.append(
        "请你作为责任人立即推进："
        "1) 拆解可执行步骤；2) 动手完成能做的部分；"
        "3) 有阻塞就写清依赖/提权；4) 回报进度（可更新 goal progress）。"
        "这不是备忘录——请真正开干并留下可验收结果。"
    )
    return "\n".join(lines)


async def _dispatch_goal_to_owner(goal: Any) -> dict[str, Any] | None:
    """有 owner 时向编制 inbox 投递工单。失败不抛，写进 dispatch 字段。

    直接走 InboxService（与 POST /kernel/inbox 同源），避免人话解析/编码问题。
    """
    owner = (getattr(goal, "owner_identity_id", None) or "").strip()
    if not owner:
        return {
            "dispatched": False,
            "reason": "no_owner",
            "message": "未指定责任 Agent，目标仅落库；请指定责任人或去员工页手动派单。",
        }
    instruction = _goal_instruction(goal)
    try:
        from backend.kernel.workforce import get_workforce_inbox

        inbox = get_workforce_inbox()
        if inbox is None:
            # 回落 assign_to_employee（会再查一次 inbox）
            from backend.agent.workforce_dispatch import assign_to_employee

            msg = await assign_to_employee(
                owner,
                instruction,
                priority=8,
                source="api",
                via="goal_auto_dispatch",
                project_title=(getattr(goal, "title", None) or "")[:80] or None,
            )
            ok = isinstance(msg, str) and (
                msg.startswith("✅") or "已派给" in msg or "status=" in msg
            )
            job_id = None
            if ok:
                import re

                m = re.search(
                    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})",
                    msg,
                )
                if m:
                    job_id = m.group(1)
            return {
                "dispatched": bool(ok),
                "owner_identity_id": owner,
                "job_id": job_id,
                "message": msg if isinstance(msg, str) else str(msg),
                "reason": None if ok else "inbox_unavailable",
            }

        title = (getattr(goal, "title", None) or "")[:80] or None
        payload: dict[str, Any] = {
            "via": "goal_auto_dispatch",
            "goal_id": str(getattr(goal, "id", "") or ""),
            "goal_kind": str(getattr(goal, "kind", "") or ""),
        }
        if title:
            payload["project_title"] = title

        item = await inbox.enqueue(
            owner,
            instruction,
            source="api",
            priority=8,
            payload=payload,
        )
        if item is None:
            return {
                "dispatched": False,
                "owner_identity_id": owner,
                "reason": "rejected",
                "message": "工单被拒收（员工非 active 或收件箱溢出）。请检查员工状态。",
            }
        name = ""
        try:
            from backend.agent.workforce_dispatch import find_identity_by_name_or_id

            ident = await find_identity_by_name_or_id(owner)
            name = getattr(ident, "name", "") or ""
        except Exception:
            pass
        return {
            "dispatched": True,
            "owner_identity_id": owner,
            "job_id": str(item.id),
            "status": getattr(item, "status", None),
            "message": (
                f"已派给「{name or owner[:8]}」工单 {item.id} "
                f"status={getattr(item, 'status', 'pending')}（dispatcher 将自动领取）"
            ),
            "reason": None,
        }
    except ValueError as e:
        return {
            "dispatched": False,
            "owner_identity_id": owner,
            "reason": "invalid",
            "message": str(e),
        }
    except Exception as e:
        logger.warning("goal auto-dispatch failed goal=%s: %s", getattr(goal, "id", None), e)
        return {
            "dispatched": False,
            "owner_identity_id": owner,
            "reason": "exception",
            "message": f"自动派单异常：{e}",
        }


@router.get("/tree")
async def get_goal_tree(
    current_user: Annotated[UserRead, Depends(get_current_user)],
) -> dict[str, Any]:
    repo = AsyncGoalRepository()
    all_goals = await repo.list_all()  # 单次查询，内存组树（消除 N+1）
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
    return {"objectives": tree, "total": len(tree)}


@router.post("")
async def create_goal(
    body: GoalCreate,
    current_user: Annotated[UserRead, Depends(get_current_user)],
) -> dict[str, Any]:
    import uuid as _uuid

    repo = AsyncGoalRepository()
    data: dict[str, Any] = {
        "title": body.title,
        "description": body.description,
        "kind": body.kind,
        "owner_identity_id": body.owner_identity_id,
        "due_date": body.due_date,
        "progress": max(0.0, min(100.0, body.progress)),
        "user_id": current_user.id if hasattr(current_user, "id") else None,
    }
    if body.parent_id:
        try:
            data["parent_id"] = _uuid.UUID(body.parent_id)
        except ValueError:
            return {"error": f"invalid parent_id: {body.parent_id}"}
    goal = await repo.create(data)
    out = goal.to_dict()
    # 目标不是备忘录：有责任人则立刻进编制收件箱，由 dispatcher 领取
    if body.auto_dispatch:
        out["dispatch"] = await _dispatch_goal_to_owner(goal)
    else:
        out["dispatch"] = {
            "dispatched": False,
            "reason": "auto_dispatch_disabled",
            "message": "已关闭自动派单",
        }
    return out


@router.put("/{goal_id}")
async def update_goal(
    goal_id: str,
    body: GoalUpdate,
    current_user: Annotated[UserRead, Depends(get_current_user)],
) -> dict[str, Any]:
    import uuid as _uuid

    repo = AsyncGoalRepository()
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    if "progress" in data:
        data["progress"] = max(0.0, min(100.0, data["progress"]))
    try:
        gid = _uuid.UUID(goal_id)
    except ValueError:
        return {"error": f"invalid goal_id: {goal_id}"}
    goal = await repo.update(gid, data)
    if goal is None:
        return {"error": f"goal not found: {goal_id}"}
    return goal.to_dict()


@router.delete("/{goal_id}")
async def delete_goal(
    goal_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
) -> dict[str, Any]:
    import uuid as _uuid

    repo = AsyncGoalRepository()
    try:
        gid = _uuid.UUID(goal_id)
    except ValueError:
        return {"deleted": False, "id": goal_id, "error": "invalid goal_id"}
    ok = await repo.delete(gid)
    return {"deleted": ok, "id": goal_id}
