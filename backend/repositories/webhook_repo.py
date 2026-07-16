"""
Webhook Repository 接口与实现
"""

import uuid
from typing import Any, Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.webhook import Webhook, WebhookDeliveryLog
from backend.repositories.base import AsyncBaseRepository, BaseRepository


class WebhookRepository(BaseRepository):
    """Webhook 仓库接口"""

    async def list_by_user(self, user_id: uuid.UUID) -> list[Webhook]:
        raise NotImplementedError

    async def list_enabled(self) -> list[Webhook]:
        raise NotImplementedError


class AsyncWebhookRepository(AsyncBaseRepository, WebhookRepository):
    """Webhook 异步仓库实现"""

    async def get_by_id(self, id: Any) -> Webhook | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(Webhook).where(Webhook.id == id))
            return result.scalar_one_or_none()
        finally:
            await self._close_session(session)

    async def create(self, data: dict[str, Any]) -> Webhook:
        session = await self._get_session()
        try:
            obj = Webhook(**data)
            session.add(obj)
            await self._maybe_commit(session)
            await session.refresh(obj)
            return obj
        finally:
            await self._close_session(session)

    async def update(self, id: Any, data: dict[str, Any]) -> Webhook | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(Webhook).where(Webhook.id == id))
            obj = result.scalar_one_or_none()
            if not obj:
                return None
            for key, value in data.items():
                setattr(obj, key, value)
            await self._maybe_commit(session)
            await session.refresh(obj)
            return obj
        finally:
            await self._close_session(session)

    async def delete(self, id: Any) -> bool:
        session = await self._get_session()
        try:
            result = await session.execute(select(Webhook).where(Webhook.id == id))
            obj = result.scalar_one_or_none()
            if not obj:
                return False
            await session.delete(obj)
            await self._maybe_commit(session)
            return True
        finally:
            await self._close_session(session)

    async def list_by_user(self, user_id: uuid.UUID) -> list[Webhook]:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(Webhook).where(Webhook.user_id == user_id).order_by(Webhook.name)
            )
            return list(result.scalars().all())
        finally:
            await self._close_session(session)

    async def list_enabled(self) -> list[Webhook]:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(Webhook).where(Webhook.enabled.is_(True)).order_by(Webhook.name)
            )
            return list(result.scalars().all())
        finally:
            await self._close_session(session)


class AsyncWebhookDeliveryLogRepository(AsyncBaseRepository):
    """Webhook 投递日志仓库"""

    async def get_by_id(self, id: Any) -> WebhookDeliveryLog | None:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(WebhookDeliveryLog).where(WebhookDeliveryLog.id == id)
            )
            return result.scalar_one_or_none()
        finally:
            await self._close_session(session)

    async def create(self, data: dict[str, Any]) -> WebhookDeliveryLog:
        session = await self._get_session()
        try:
            obj = WebhookDeliveryLog(**data)
            session.add(obj)
            await self._maybe_commit(session)
            await session.refresh(obj)
            return obj
        finally:
            await self._close_session(session)

    async def update(self, id: Any, data: dict[str, Any]) -> WebhookDeliveryLog | None:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(WebhookDeliveryLog).where(WebhookDeliveryLog.id == id)
            )
            obj = result.scalar_one_or_none()
            if not obj:
                return None
            for key, value in data.items():
                setattr(obj, key, value)
            await self._maybe_commit(session)
            await session.refresh(obj)
            return obj
        finally:
            await self._close_session(session)

    async def delete(self, id: Any) -> bool:
        return True  # 日志不单独删除

    async def list_by_webhook(
        self, webhook_id: uuid.UUID, limit: int = 50
    ) -> list[WebhookDeliveryLog]:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(WebhookDeliveryLog)
                .where(WebhookDeliveryLog.webhook_id == webhook_id)
                .order_by(WebhookDeliveryLog.created_at.desc())
                .limit(limit)
            )
            return list(result.scalars().all())
        finally:
            await self._close_session(session)
