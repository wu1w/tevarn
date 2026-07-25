"""AgentRun / RunStep 仓库（Phase 0.5.2 Durable Execution）

沿用 AsyncBaseRepository 模式：默认自建 per-call session（可被 recorder 单例使用），
也支持 UnitOfWork 注入 session。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, desc

from backend.models.agent_run import AgentRun, RunStep
from backend.repositories.base import AsyncBaseRepository


class AsyncAgentRunRepository(AsyncBaseRepository):
    """AgentRun + RunStep 的异步存取"""

    # ─────────── Run ───────────

    async def create_run(self, data: dict[str, Any]) -> AgentRun:
        session = await self._get_session()
        try:
            obj = AgentRun(**data)
            session.add(obj)
            await self._maybe_commit(session)
            await session.refresh(obj)
            return obj
        except Exception:
            await session.rollback()
            raise
        finally:
            await self._close_session(session)

    async def get_run(self, run_id: uuid.UUID) -> AgentRun | None:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(AgentRun).where(AgentRun.id == run_id)
            )
            return result.scalar_one_or_none()
        finally:
            await self._close_session(session)

    async def update_run(self, run_id: uuid.UUID, data: dict[str, Any]) -> AgentRun | None:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(AgentRun).where(AgentRun.id == run_id)
            )
            obj = result.scalar_one_or_none()
            if obj is None:
                return None
            for k, v in data.items():
                setattr(obj, k, v)
            await self._maybe_commit(session)
            await session.refresh(obj)
            return obj
        except Exception:
            await session.rollback()
            raise
        finally:
            await self._close_session(session)

    async def list_runs(
        self,
        session_id: uuid.UUID,
        limit: int = 20,
        offset: int = 0,
    ) -> list[AgentRun]:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(AgentRun)
                .where(AgentRun.session_id == session_id)
                .order_by(desc(AgentRun.created_at))
                .limit(limit)
                .offset(offset)
            )
            return list(result.scalars().all())
        finally:
            await self._close_session(session)

    # ─────────── Step ───────────

    async def add_step(self, data: dict[str, Any]) -> RunStep:
        session = await self._get_session()
        try:
            obj = RunStep(**data)
            session.add(obj)
            await self._maybe_commit(session)
            await session.refresh(obj)
            return obj
        except Exception:
            await session.rollback()
            raise
        finally:
            await self._close_session(session)

    async def list_steps(self, run_id: uuid.UUID, limit: int = 500) -> list[RunStep]:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(RunStep)
                .where(RunStep.run_id == run_id)
                .order_by(RunStep.seq)
                .limit(limit)
            )
            return list(result.scalars().all())
        finally:
            await self._close_session(session)


def utcnow() -> datetime:
    """统一的 UTC 时间戳（recorder / API 共用）"""
    return datetime.now(timezone.utc)
