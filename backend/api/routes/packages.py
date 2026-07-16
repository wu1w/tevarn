"""Takton Package API — 统一 skill / 子代理 / 工作流挂载。"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.packages.loader import (
    get_package_by_name,
    list_all_packages,
    package_to_detail,
    package_to_list_item,
    resolve_attached_snippets,
)
from backend.packages.session_packages import (
    attach_package,
    detach_package,
    get_session_attached_packages,
    set_session_attached_packages,
)
from backend.schemas.user import UserRead

from ..dependencies import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/packages", tags=["Packages"])


class AttachBody(BaseModel):
    session_id: str
    name: str


class SetAttachedBody(BaseModel):
    session_id: str
    packages: list[str] = Field(default_factory=list)


@router.get("")
async def list_packages(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    session_id: str | None = Query(default=None),
    source: str | None = Query(default=None, description="workspace|skill|sub_agent|workflow"),
):
    pkgs = await list_all_packages()
    attached: list[str] = []
    if session_id:
        try:
            attached = await get_session_attached_packages(session_id)
        except Exception:
            attached = []
    att_set = set(attached)
    items = []
    for p in pkgs:
        if source and p.source != source:
            continue
        items.append(package_to_list_item(p, attached=p.name in att_set).model_dump())
    return {
        "packages": items,
        "attached": attached,
        "count": len(items),
    }


@router.get("/session/{session_id}")
async def get_session_packages(
    session_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    attached = await get_session_attached_packages(session_id)
    snippets = await resolve_attached_snippets(attached)
    return {"session_id": session_id, "attached": attached, "snippets": snippets}


@router.post("/attach")
async def attach_pkg(
    body: AttachBody,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    pkgs = await list_all_packages()
    if not get_package_by_name(pkgs, body.name):
        raise HTTPException(status_code=404, detail=f"package `{body.name}` not found")
    try:
        attached = await attach_package(body.session_id, body.name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    snippets = await resolve_attached_snippets(attached)
    return {
        "ok": True,
        "attached": attached,
        "snippets": snippets,
        "message": f"已挂载包 `{body.name}`",
    }


@router.post("/detach")
async def detach_pkg(
    body: AttachBody,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    try:
        attached = await detach_package(body.session_id, body.name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {
        "ok": True,
        "attached": attached,
        "message": f"已卸载包 `{body.name}`",
    }


@router.put("/session")
async def set_session_packages(
    body: SetAttachedBody,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    pkgs = await list_all_packages()
    known = {p.name for p in pkgs}
    unknown = [n for n in body.packages if n not in known]
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown packages: {unknown}")
    try:
        attached = await set_session_attached_packages(body.session_id, body.packages)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    snippets = await resolve_attached_snippets(attached)
    return {"ok": True, "attached": attached, "snippets": snippets}


@router.get("/detail/{name:path}")
async def get_package(
    name: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    session_id: str | None = Query(default=None),
):
    pkgs = await list_all_packages()
    p = get_package_by_name(pkgs, name)
    if not p:
        raise HTTPException(status_code=404, detail=f"package `{name}` not found")
    attached = False
    if session_id:
        att = await get_session_attached_packages(session_id)
        attached = name in att
    return package_to_detail(p, attached=attached).model_dump()
