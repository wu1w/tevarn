"""消息 / AgentRun 保留期清理（P2 审计）。

默认关闭（days=0）；开启后按 created_at 删除过期行。
在 lifespan 后台周期任务中调用。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _days(name: str, default: int = 0) -> int:
    try:
        from backend.core.config import settings

        return int(getattr(settings, name, default) or 0)
    except Exception:
        return default


async def purge_expired_messages(*, days: int | None = None) -> int:
    """删除 created_at 早于 cutoff 的消息。返回删除行数。"""
    d = _days("message_retention_days") if days is None else int(days)
    if d <= 0:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=d)
    from sqlalchemy import delete

    from backend.database import AsyncSessionLocal
    from backend.models.message import Message

    async with AsyncSessionLocal() as session:
        res = await session.execute(
            delete(Message).where(Message.created_at < cutoff)
        )
        await session.commit()
        n = int(res.rowcount or 0)
        if n:
            logger.info("retention: purged %s messages older than %s days", n, d)
        return n


async def purge_expired_agent_runs(*, days: int | None = None) -> int:
    """删除终态且 created_at 早于 cutoff 的 agent_runs（及 cascade run_steps）。"""
    d = _days("agent_run_retention_days") if days is None else int(days)
    if d <= 0:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=d)
    from sqlalchemy import delete

    from backend.database import AsyncSessionLocal
    from backend.models.agent_run import AgentRun

    terminal = ("done", "failed", "cancelled")
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            delete(AgentRun).where(
                AgentRun.created_at < cutoff,
                AgentRun.status.in_(terminal),
            )
        )
        await session.commit()
        n = int(res.rowcount or 0)
        if n:
            logger.info("retention: purged %s agent_runs older than %s days", n, d)
        return n


async def run_retention_pass() -> dict[str, Any]:
    """单次清理入口。"""
    out = {
        "messages": await purge_expired_messages(),
        "agent_runs": await purge_expired_agent_runs(),
    }
    return out


async def retention_loop(*, interval_hours: float = 24.0) -> None:
    """后台周期清理（默认每天一次）。"""
    import asyncio

    # 启动后稍等，避免冷启动抢 DB
    await asyncio.sleep(30)
    while True:
        try:
            await run_retention_pass()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("retention pass failed: %s", e)
        await asyncio.sleep(max(3600.0, float(interval_hours) * 3600.0))
