"""ClusterRun 仓库（Phase 3：cluster 结果持久化）

沿用 AsyncBaseRepository 模式：默认自建 per-call session。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, desc, update

from backend.models.cluster_run import ClusterRun
from backend.repositories.base import AsyncBaseRepository


class AsyncClusterRunRepository(AsyncBaseRepository):
    """ClusterRun 的异步存取"""

    async def create_run(self, data: dict[str, Any]) -> ClusterRun:
        session = await self._get_session()
        try:
            obj = ClusterRun(**data)
            session.add(obj)
            await self._maybe_commit(session)
            await session.refresh(obj)
            return obj
        except Exception:
            await session.rollback()
            raise
        finally:
            await self._close_session(session)

    async def get_by_task_id(self, task_id: str) -> ClusterRun | None:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(ClusterRun).where(ClusterRun.task_id == task_id)
            )
            return result.scalars().first()
        finally:
            await self._close_session(session)

    async def finish_run(
        self,
        task_id: str,
        *,
        status: str,
        sub_tasks: list[dict[str, Any]] | None = None,
        aggregated_result: dict[str, Any] | None = None,
        review: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        session = await self._get_session()
        try:
            await session.execute(
                update(ClusterRun)
                .where(ClusterRun.task_id == task_id)
                .values(
                    status=status,
                    sub_tasks=sub_tasks,
                    aggregated_result=aggregated_result,
                    review=review,
                    error=error,
                    ended_at=datetime.now(timezone.utc),
                )
            )
            await self._maybe_commit(session)
        except Exception:
            await session.rollback()
            raise
        finally:
            await self._close_session(session)

    async def list_recent(self, limit: int = 50) -> list[ClusterRun]:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(ClusterRun)
                .order_by(desc(ClusterRun.created_at))
                .limit(limit)
            )
            return list(result.scalars().all())
        finally:
            await self._close_session(session)

    async def mark_interrupted_running(self) -> int:
        """启动清扫：仍是 running 的记录 → interrupted（服务重启，不假装还在跑）"""
        session = await self._get_session()
        try:
            result = await session.execute(
                update(ClusterRun)
                .where(ClusterRun.status == "running")
                .values(
                    status="interrupted",
                    error="server restarted while cluster was running",
                    ended_at=datetime.now(timezone.utc),
                )
            )
            await self._maybe_commit(session)
            return int(result.rowcount or 0)
        except Exception:
            await session.rollback()
            raise
        finally:
            await self._close_session(session)
