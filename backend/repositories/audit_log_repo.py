"""
Audit Log Repository 接口与实现
"""

import uuid
from abc import abstractmethod
from typing import Any

from sqlalchemy import desc, func, select

from backend.models.audit_log import AuditLog
from backend.schemas.audit_log import AuditLogRead

from .base import AsyncBaseRepository, BaseRepository


class AuditLogRepository(BaseRepository):
    """审计日志仓库接口"""

    @abstractmethod
    async def create_log(
        self,
        action: str,
        user_id: uuid.UUID | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        details: dict[str, Any] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        success: bool = True,
    ) -> AuditLogRead:
        raise NotImplementedError

    @abstractmethod
    async def list_logs(
        self, limit: int = 200, offset: int = 0
    ) -> tuple[list[AuditLogRead], int]:
        raise NotImplementedError


class AsyncAuditLogRepository(AsyncBaseRepository, AuditLogRepository):
    """基于 SQLAlchemy async session 的审计日志仓库实现"""

    async def get_by_id(self, id: Any) -> AuditLogRead | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(AuditLog).where(AuditLog.id == id))
            obj = result.scalar_one_or_none()
            return AuditLogRead.model_validate(obj) if obj else None
        finally:
            await self._close_session(session)

    async def create(self, data: dict[str, Any]) -> AuditLogRead:
        session = await self._get_session()
        try:
            obj = AuditLog(**data)
            session.add(obj)
            await self._maybe_commit(session)
            await session.refresh(obj)
            return AuditLogRead.model_validate(obj)
        finally:
            await self._close_session(session)

    async def update(self, id: Any, data: dict[str, Any]) -> AuditLogRead | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(AuditLog).where(AuditLog.id == id))
            obj = result.scalar_one_or_none()
            if not obj:
                return None
            for key, value in data.items():
                setattr(obj, key, value)
            await self._maybe_commit(session)
            await session.refresh(obj)
            return AuditLogRead.model_validate(obj)
        finally:
            await self._close_session(session)

    async def delete(self, id: Any) -> bool:
        session = await self._get_session()
        try:
            result = await session.execute(select(AuditLog).where(AuditLog.id == id))
            obj = result.scalar_one_or_none()
            if not obj:
                return False
            await session.delete(obj)
            await self._maybe_commit(session)
            return True
        finally:
            await self._close_session(session)

    async def create_log(
        self,
        action: str,
        user_id: uuid.UUID | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        details: dict[str, Any] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        success: bool = True,
    ) -> AuditLogRead:
        return await self.create({
            "user_id": user_id,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "details": details or {},
            "ip_address": ip_address,
            "user_agent": user_agent,
            "success": success,
        })

    async def list_logs(
        self, limit: int = 200, offset: int = 0
    ) -> tuple[list[AuditLogRead], int]:
        session = await self._get_session()
        try:
            stmt = select(AuditLog).order_by(desc(AuditLog.created_at))
            count_result = await session.execute(
                select(func.count()).select_from(stmt.subquery())
            )
            total = count_result.scalar_one()

            result = await session.execute(stmt.offset(offset).limit(limit))
            items = [AuditLogRead.model_validate(obj) for obj in result.scalars().all()]
            return items, total
        finally:
            await self._close_session(session)
