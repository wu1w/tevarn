"""Evolution API — status, assets, stats, tasks, enable, delete."""

from __future__ import annotations

from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.evolution import store
from backend.evolution.config import get_evolution_config, set_evolution_config
from backend.evolution.manager import get_evolution_manager
from backend.schemas.user import UserRead

from ..dependencies import get_current_user

router = APIRouter(prefix="/evolution", tags=["Evolution"])


class EnableBody(BaseModel):
    enabled: bool = True
    auto_apply_skills: Optional[bool] = None
    mode: Optional[str] = None


class BulkDeleteBody(BaseModel):
    ids: list[str] = Field(default_factory=list)
    filter: Optional[str] = None  # unused_auto


@router.get("/status")
async def evolution_status(
    current_user: Annotated[UserRead, Depends(get_current_user)],
) -> dict[str, Any]:
    mgr = get_evolution_manager()
    return mgr.status()


@router.get("/stats")
async def evolution_stats(
    current_user: Annotated[UserRead, Depends(get_current_user)],
) -> dict[str, Any]:
    get_evolution_manager().ensure_seeded()
    return store.stats()


@router.post("/enable")
async def evolution_enable(
    body: EnableBody,
    current_user: Annotated[UserRead, Depends(get_current_user)],
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"enabled": body.enabled}
    if body.auto_apply_skills is not None:
        kwargs["auto_apply_skills"] = body.auto_apply_skills
    if body.mode is not None:
        kwargs["mode"] = body.mode
    set_evolution_config(**kwargs)
    return get_evolution_manager().status()


@router.get("/assets")
async def list_assets(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    kind: Optional[str] = None,
    status: Optional[str] = None,
    source: Optional[str] = None,
    unused_only: bool = False,
    sort: str = Query("updated_at"),
    limit: int = Query(200, ge=1, le=500),
) -> list[dict[str, Any]]:
    get_evolution_manager().ensure_seeded()
    return store.list_assets(
        kind=kind,
        status=status,
        source=source,
        unused_only=unused_only,
        sort=sort,
        limit=limit,
    )


@router.get("/assets/{asset_id}")
async def get_asset(
    asset_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
) -> dict[str, Any]:
    a = store.get_asset(asset_id)
    if not a:
        raise HTTPException(404, "asset not found")
    return a


@router.delete("/assets/{asset_id}")
async def delete_asset(
    asset_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
) -> dict[str, Any]:
    a = store.get_asset(asset_id)
    if not a:
        raise HTTPException(404, "asset not found")
    if a.get("source") == "seed":
        raise HTTPException(400, "预置资产不可删除")
    ok = store.delete_asset(asset_id)
    if not ok:
        raise HTTPException(400, "删除失败")
    try:
        from backend.evolution.runtime_tools import unregister_evolved_tool

        unregister_evolved_tool(a["name"])
    except Exception:
        pass
    return {"ok": True, "id": asset_id}


@router.post("/assets/bulk_delete")
async def bulk_delete(
    body: BulkDeleteBody,
    current_user: Annotated[UserRead, Depends(get_current_user)],
) -> dict[str, Any]:
    deleted = 0
    if body.filter == "unused_auto":
        deleted = store.bulk_delete_unused_auto()
    for i in body.ids:
        a = store.get_asset(i)
        if a and a.get("source") != "seed":
            if store.delete_asset(i):
                deleted += 1
    return {"ok": True, "deleted": deleted}


@router.post("/assets/{asset_id}/enable")
async def enable_asset(
    asset_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
) -> dict[str, Any]:
    a = store.update_asset_status(asset_id, "active")
    if not a:
        raise HTTPException(404, "asset not found")
    try:
        from backend.evolution.runtime_tools import register_evolved_tool

        register_evolved_tool(
            name=a["name"],
            description=a.get("summary") or a["name"],
            body=a.get("content") or "",
            asset_id=a.get("id"),
            enabled=True,
        )
    except Exception:
        pass
    return a


@router.post("/assets/{asset_id}/disable")
async def disable_asset(
    asset_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
) -> dict[str, Any]:
    a = store.update_asset_status(asset_id, "disabled")
    if not a:
        raise HTTPException(404, "asset not found")
    try:
        from backend.evolution.runtime_tools import unregister_evolved_tool

        unregister_evolved_tool(a["name"])
    except Exception:
        pass
    return a


@router.post("/drafts/{asset_id}/apply")
async def apply_draft(
    asset_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
) -> dict[str, Any]:
    a = store.get_asset(asset_id)
    if not a:
        raise HTTPException(404, "asset not found")
    from backend.evolution.gates import run_gates

    gate = run_gates(
        name=a["name"],
        content=a.get("content") or "",
        summary=a.get("summary") or "",
        score=a.get("last_score"),
        baseline_score=0.5,
    )
    if not gate["ok"]:
        raise HTTPException(400, detail={"message": "未过安全门", "gates": gate})
    updated = store.update_asset_status(asset_id, "active")
    if updated:
        try:
            from backend.evolution.runtime_tools import register_evolved_tool

            register_evolved_tool(
                name=updated["name"],
                description=updated.get("summary") or updated["name"],
                body=updated.get("content") or "",
                asset_id=updated.get("id"),
                enabled=True,
            )
        except Exception:
            pass
    return {"ok": True, "asset": updated, "gate": gate}


@router.post("/drafts/{asset_id}/reject")
async def reject_draft(
    asset_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
) -> dict[str, Any]:
    a = store.update_asset_status(asset_id, "rejected")
    if not a:
        raise HTTPException(404, "asset not found")
    return a


@router.get("/tasks")
async def list_tasks(
    current_user: Annotated[UserRead, Depends(get_current_user)],
) -> list[dict[str, Any]]:
    get_evolution_manager().ensure_seeded()
    return store.list_tasks()


@router.post("/run_task/{name}")
async def run_task(
    name: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
) -> dict[str, Any]:
    return await get_evolution_manager().run_task(name)
