"""P4 Curator: stale → archive unused auto skills (never delete seeds)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.evolution import store
from backend.evolution.config import get_evolution_config

logger = logging.getLogger(__name__)


def run_curator(*, dry_run: bool = False) -> dict[str, Any]:
    cfg = get_evolution_config()
    if not cfg.curator_enabled:
        return {"ok": True, "skipped": True, "reason": "curator_disabled"}

    now = datetime.now(timezone.utc)
    stale_before = (now - timedelta(days=max(1, cfg.curator_stale_days))).isoformat()
    archive_before = (now - timedelta(days=max(1, cfg.curator_archive_days))).isoformat()

    assets = store.list_assets(source="auto", limit=500)
    marked_stale: list[str] = []
    archived: list[str] = []

    for a in assets:
        if a.get("source") == "seed":
            continue
        if a.get("status") in {"archived", "rejected"}:
            continue
        # pinned?
        meta = a.get("meta") or {}
        if meta.get("pinned"):
            continue
        use = int(a.get("use_count") or 0)
        updated = a.get("updated_at") or a.get("created_at") or ""
        last_used = a.get("last_used_at") or updated

        # archive: old + unused
        if use == 0 and last_used and last_used < archive_before:
            if not dry_run:
                store.update_asset_status(a["id"], "archived")
                try:
                    from backend.evolution.runtime_tools import unregister_evolved_tool

                    unregister_evolved_tool(a["name"])
                except Exception:
                    pass
            archived.append(a["id"])
            continue

        # stale mark via meta
        if use == 0 and last_used and last_used < stale_before and a.get("status") == "active":
            if not dry_run:
                meta = dict(meta)
                meta["stale"] = True
                meta["stale_at"] = now.isoformat()
                store.patch_asset_meta(a["id"], meta)
            marked_stale.append(a["id"])

    logger.info("curator: stale=%s archived=%s dry_run=%s", len(marked_stale), len(archived), dry_run)
    return {
        "ok": True,
        "dry_run": dry_run,
        "stale": marked_stale,
        "archived": archived,
        "stale_count": len(marked_stale),
        "archived_count": len(archived),
    }
