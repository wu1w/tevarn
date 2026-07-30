"""Goals 路由 - O-KR 目标树 CRUD

GET    /goals/tree        目标树（O + 各自 KR + O 进度=KR 均值）
POST   /goals             创建（objective 或 key_result）
PUT    /goals/{id}        更新（title/description/status/progress/owner/due_date）
DELETE /goals/{id}        删除（objective 级联删除 KR）
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from backend.repositories.goal_repo import AsyncGoalRepository
from backend.schemas.user import UserRead

from ..dependencies import get_current_user

router = APIRouter(prefix="/goals", tags=["goals"])


class GoalCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    kind: str = "objective"  # objective / key_result
    parent_id: str | None = None
    owner_identity_id: str | None = None
    due_date: str | None = None
    progress: float = 0.0


class GoalUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None
    progress: float | None = None
    owner_identity_id: str | None = None
    due_date: str | None = None


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
    return goal.to_dict()


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
