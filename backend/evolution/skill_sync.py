"""Sync evolution assets <-> skills table + runtime tools.

Rules:
- Auto skill/tool assets (source=auto) can be mirrored into `skills` rows.
- Delete/archive from evolution MUST remove the matching skills row.
- Skills deleted via Skills API (if evolved) remove evolution assets by name.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _is_evolved_handler(cfg: dict[str, Any] | None) -> bool:
    c = cfg or {}
    return bool(c.get("evolution") or c.get("kind") in {"evolved_playbook", "evolved_skill", "evolved_tool"})


async def upsert_skill_from_asset(
    *,
    name: str,
    summary: str,
    content: str,
    asset_id: str | None = None,
    kind: str = "skill",
    enabled: bool = True,
) -> dict[str, Any]:
    """Create/update skills row for an evolution asset. Never touches is_builtin."""
    from backend.repositories.skill_repo import AsyncSkillRepository

    repo = AsyncSkillRepository()
    existing = await repo.get_skill_by_name(name)
    if existing and getattr(existing, "is_builtin", False):
        return {"ok": False, "reason": "builtin_protected", "name": name}

    payload = {
        "name": name[:64],
        "description": (summary or name)[:500],
        "schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "用户问题或上下文",
                }
            },
        },
        "enabled": bool(enabled),
        "is_builtin": False,
        "handler": "python",
        "handler_config": {
            "evolution": True,
            "kind": "evolved_playbook",
            "asset_kind": kind,
            "asset_id": asset_id,
            "body": (content or "")[:12000],
            "source": "evolution",
        },
    }
    if existing:
        await repo.update(existing.id, payload)
        skill_id = str(existing.id)
        action = "updated"
    else:
        skill = await repo.create(payload)
        skill_id = str(skill.id)
        action = "created"

    # runtime playbook tool when enabled
    try:
        from backend.evolution.runtime_tools import register_evolved_tool, unregister_evolved_tool

        if enabled:
            register_evolved_tool(
                name=name,
                description=summary or name,
                body=content or "",
                asset_id=asset_id,
                enabled=True,
            )
        else:
            unregister_evolved_tool(name)
    except Exception as e:
        logger.warning("runtime tool sync failed for %s: %s", name, e)

    return {"ok": True, "action": action, "skill_id": skill_id, "name": name}


async def delete_skill_by_name(name: str, *, only_evolved: bool = True) -> dict[str, Any]:
    """Delete skills row by name. Protect builtins."""
    from backend.repositories.skill_repo import AsyncSkillRepository

    if not name:
        return {"ok": False, "reason": "empty_name"}
    repo = AsyncSkillRepository()
    existing = await repo.get_skill_by_name(name)
    if not existing:
        # still unregister runtime
        try:
            from backend.evolution.runtime_tools import unregister_evolved_tool

            unregister_evolved_tool(name)
        except Exception:
            pass
        return {"ok": True, "action": "missing", "name": name}

    if getattr(existing, "is_builtin", False):
        return {"ok": False, "reason": "builtin_protected", "name": name}

    if only_evolved and not _is_evolved_handler(getattr(existing, "handler_config", None) or {}):
        return {"ok": False, "reason": "not_evolved", "name": name}

    await repo.delete(existing.id)
    try:
        from backend.evolution.runtime_tools import unregister_evolved_tool

        unregister_evolved_tool(name)
    except Exception:
        pass
    return {"ok": True, "action": "deleted", "name": name, "skill_id": str(existing.id)}


async def remove_evolution_assets_for_skill_name(name: str) -> int:
    """When Skills UI deletes an evolved skill, drop matching evolution assets."""
    from backend.evolution import store

    n = 0
    for a in store.list_assets(source="auto", limit=500):
        if a.get("name") == name and a.get("source") != "seed":
            if store.delete_asset(a["id"]):
                n += 1
                try:
                    from backend.evolution.runtime_tools import unregister_evolved_tool

                    unregister_evolved_tool(name)
                except Exception:
                    pass
    return n


async def purge_asset(asset: dict[str, Any]) -> dict[str, Any]:
    """Full purge: evolution asset already deleted or about to be — clean skills + tools."""
    name = asset.get("name") or ""
    skill_res = await delete_skill_by_name(name, only_evolved=True)
    return {"name": name, "skill": skill_res}
