"""会话级 package 挂载状态（存在 Session.config 中）。"""

from __future__ import annotations

import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)

CONFIG_KEY = "attached_packages"


async def get_session_attached_packages(session_id: uuid.UUID | str) -> list[str]:
    try:
        from backend.repositories.session_repo import AsyncSessionRepository

        repo = AsyncSessionRepository()
        sid = uuid.UUID(str(session_id))
        sess = await repo.get_by_id(sid)
        if not sess:
            return []
        cfg = getattr(sess, "config", None) or {}
        if not isinstance(cfg, dict):
            return []
        raw = cfg.get(CONFIG_KEY) or []
        if not isinstance(raw, list):
            return []
        return [str(x) for x in raw if x]
    except Exception as e:
        logger.warning("get attached packages failed: %s", e)
        return []


async def set_session_attached_packages(session_id: uuid.UUID | str, names: list[str]) -> list[str]:
    from backend.repositories.session_repo import AsyncSessionRepository

    repo = AsyncSessionRepository()
    sid = uuid.UUID(str(session_id))
    sess = await repo.get_by_id(sid)
    if not sess:
        raise ValueError(f"session {session_id} not found")
    cfg: dict[str, Any] = dict(getattr(sess, "config", None) or {})
    # 去重保序
    seen: set[str] = set()
    clean: list[str] = []
    for n in names:
        n = str(n).strip()
        if not n or n in seen:
            continue
        seen.add(n)
        clean.append(n)
    cfg[CONFIG_KEY] = clean
    await repo.update(sid, {"config": cfg})
    return clean


async def attach_package(session_id: uuid.UUID | str, name: str) -> list[str]:
    cur = await get_session_attached_packages(session_id)
    if name not in cur:
        cur.append(name)
    return await set_session_attached_packages(session_id, cur)


async def detach_package(session_id: uuid.UUID | str, name: str) -> list[str]:
    cur = await get_session_attached_packages(session_id)
    cur = [x for x in cur if x != name]
    return await set_session_attached_packages(session_id, cur)
