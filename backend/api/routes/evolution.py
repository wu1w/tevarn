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
    auto_apply_tools: Optional[bool] = None
    mode: Optional[str] = None
    from_cron: Optional[bool] = None
    from_tasks: Optional[bool] = None
    auto_observe: Optional[bool] = None
    auto_create_tools: Optional[bool] = None
    curator_enabled: Optional[bool] = None


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
    for key in (
        "auto_apply_skills",
        "auto_apply_tools",
        "mode",
        "from_cron",
        "from_tasks",
        "auto_observe",
        "auto_create_tools",
        "curator_enabled",
    ):
        val = getattr(body, key, None)
        if val is not None:
            kwargs[key] = val
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
    skill_sync_res = None
    try:
        from backend.evolution.skill_sync import purge_asset

        skill_sync_res = await purge_asset(a)
    except Exception as e:
        skill_sync_res = {"error": str(e)}
    return {"ok": True, "id": asset_id, "name": a.get("name"), "skill_sync": skill_sync_res}


@router.post("/assets/bulk_delete")
async def bulk_delete(
    body: BulkDeleteBody,
    current_user: Annotated[UserRead, Depends(get_current_user)],
) -> dict[str, Any]:
    deleted = 0
    skill_names: list[str] = []
    to_purge: list[dict[str, Any]] = []

    if body.filter == "unused_auto":
        for a in store.list_assets(source="auto", unused_only=True, limit=500):
            if a.get("source") != "seed":
                to_purge.append(a)
        store.bulk_delete_unused_auto()
        deleted += len(to_purge)

    for i in body.ids:
        a = store.get_asset(i)
        if a and a.get("source") != "seed":
            to_purge.append(a)
            if store.delete_asset(i):
                deleted += 1

    from backend.evolution.skill_sync import purge_asset

    for a in to_purge:
        try:
            r = await purge_asset(a)
            if a.get("name"):
                skill_names.append(a["name"])
        except Exception:
            pass
    return {"ok": True, "deleted": deleted, "skill_names": skill_names}


@router.post("/assets/{asset_id}/enable")
async def enable_asset(
    asset_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
) -> dict[str, Any]:
    a = store.update_asset_status(asset_id, "active")
    if not a:
        raise HTTPException(404, "asset not found")
    try:
        from backend.evolution.skill_sync import upsert_skill_from_asset

        await upsert_skill_from_asset(
            name=a["name"],
            summary=a.get("summary") or a["name"],
            content=a.get("content") or "",
            asset_id=a.get("id"),
            kind=a.get("kind") or "skill",
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
        from backend.evolution.skill_sync import upsert_skill_from_asset

        await upsert_skill_from_asset(
            name=a["name"],
            summary=a.get("summary") or a["name"],
            content=a.get("content") or "",
            asset_id=a.get("id"),
            kind=a.get("kind") or "skill",
            enabled=False,
        )
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
            from backend.evolution.skill_sync import upsert_skill_from_asset

            await upsert_skill_from_asset(
                name=updated["name"],
                summary=updated.get("summary") or updated["name"],
                content=updated.get("content") or "",
                asset_id=updated.get("id"),
                kind=updated.get("kind") or "skill",
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
    a = store.get_asset(asset_id)
    if not a:
        raise HTTPException(404, "asset not found")
    updated = store.update_asset_status(asset_id, "rejected")
    try:
        from backend.evolution.skill_sync import delete_skill_by_name

        await delete_skill_by_name(a["name"], only_evolved=True)
    except Exception:
        pass
    return updated


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
    return await get_evolution_manager().run_task(name, improve=True)


class TaskOutcomeBody(BaseModel):
    task_name: str
    success: bool = True
    detail: str = ""
    failure_codes: list[str] = Field(default_factory=list)
    source: str = "task"


@router.post("/from_task")
async def from_task_outcome(
    body: TaskOutcomeBody,
    current_user: Annotated[UserRead, Depends(get_current_user)],
) -> dict[str, Any]:
    """P1: external task/cron-like outcome → evolution assets."""
    res = await get_evolution_manager().run_from_task_outcome(
        task_name=body.task_name,
        success=body.success,
        detail=body.detail,
        failure_codes=body.failure_codes,
        source=body.source or "task",
    )
    return res or {"ok": False, "reason": "evolution_disabled"}


@router.get("/clusters")
async def list_clusters(
    current_user: Annotated[UserRead, Depends(get_current_user)],
) -> list[dict[str, Any]]:
    get_evolution_manager().ensure_seeded()
    return store.list_clusters(50)


@router.post("/curator/run")
async def curator_run(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    dry_run: bool = False,
) -> dict[str, Any]:
    return await get_evolution_manager().run_curator(dry_run=dry_run)


@router.get("/version")
async def evolution_version(
    current_user: Annotated[UserRead, Depends(get_current_user)],
) -> dict[str, Any]:
    from backend.evolution.config import ENGINE_VERSION, get_evolution_config

    cfg = get_evolution_config()
    return {
        "engine_version": ENGINE_VERSION,
        "product_target": "0.1.1",
        "phases": ["P1_tasks", "P2_skill_md", "P3_tool_draft", "P4_observe_curator"],
        "enabled": cfg.enabled,
    }
