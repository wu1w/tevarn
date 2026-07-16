"""Workspace API — 项目文件夹绑定 / 树 / 终端执行。"""
from __future__ import annotations

from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.api.dependencies import get_current_user
from backend.schemas.user import UserRead
from backend.workspace import service as ws

router = APIRouter(prefix="/workspace", tags=["Workspace"])


class BindBody(BaseModel):
    root: str = Field(..., description="项目根绝对路径")


class ExecBody(BaseModel):
    command: str
    timeout: float = 120.0


def _uid(user: UserRead) -> str:
    return str(getattr(user, "id", None) or user.email or "default")


@router.get("")
async def get_workspace(
    current_user: Annotated[UserRead, Depends(get_current_user)],
) -> dict[str, Any]:
    root = ws.get_root(_uid(current_user))
    if not root:
        return {"bound": False, "root": None, "name": None}
    return {"bound": True, "root": str(root), "name": root.name}


@router.post("/bind")
async def bind_workspace(
    body: BindBody,
    current_user: Annotated[UserRead, Depends(get_current_user)],
) -> dict[str, Any]:
    try:
        root = ws.set_root(_uid(current_user), body.root)
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid path: {e}") from e
    return {"bound": True, "root": str(root), "name": root.name}


@router.post("/unbind")
async def unbind_workspace(
    current_user: Annotated[UserRead, Depends(get_current_user)],
) -> dict[str, Any]:
    ws.clear_root(_uid(current_user))
    return {"bound": False}


@router.get("/tree")
async def workspace_tree(
    path: str = "",
    depth: int = 2,
    current_user: Annotated[UserRead, Depends(get_current_user)] = None,
) -> list[dict[str, Any]]:
    root = ws.get_root(_uid(current_user))
    if not root:
        raise HTTPException(status_code=400, detail="No workspace bound. Open a project folder first.")
    depth = max(1, min(depth, 3))
    try:
        target = ws.resolve_under_root(root, path)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Access denied")
    if not target.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")
    return ws.build_tree(target, root, max_depth=depth)


@router.post("/exec")
async def workspace_exec(
    body: ExecBody,
    current_user: Annotated[UserRead, Depends(get_current_user)],
) -> dict[str, Any]:
    root = ws.get_root(_uid(current_user))
    if not root:
        raise HTTPException(status_code=400, detail="No workspace bound")
    result = await ws.exec_command(root, body.command, timeout=body.timeout)
    return result
