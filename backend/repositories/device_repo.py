"""
Device Repository 接口与实现
"""

import uuid
from abc import abstractmethod
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from backend.models.device import Device
from backend.schemas.device import DeviceRead

from .base import AsyncBaseRepository, BaseRepository


class DeviceRepository(BaseRepository):
    """Device 仓库接口"""

    @abstractmethod
    async def list_by_type(self, device_type: str) -> list[Any]:
        """按类型列出设备"""
        raise NotImplementedError

    @abstractmethod
    async def list_by_user(self, user_id: uuid.UUID) -> list[Any]:
        """列出指定用户的所有设备"""
        raise NotImplementedError

    @abstractmethod
    async def update_status(self, device_id: uuid.UUID, status: str) -> Any | None:
        """更新设备状态"""
        raise NotImplementedError

    @abstractmethod
    async def heartbeat(self, device_id: uuid.UUID) -> Any | None:
        """更新设备最后活跃时间"""
        raise NotImplementedError


class AsyncDeviceRepository(AsyncBaseRepository, DeviceRepository):
    """基于 SQLAlchemy async session 的 Device 仓库实现"""


    async def get_by_id(self, id: Any) -> DeviceRead | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(Device).where(Device.id == id))
            device = result.scalar_one_or_none()
            return DeviceRead.model_validate(device) if device is not None else None
        finally:
            await self._close_session(session)

    async def create(self, data: dict[str, Any]) -> DeviceRead:
        session = await self._get_session()
        try:
            device = Device(**data)
            session.add(device)
            await self._maybe_commit(session)
            await session.refresh(device)
            return DeviceRead.model_validate(device)
        finally:
            await self._close_session(session)

    async def update(self, id: Any, data: dict[str, Any]) -> DeviceRead | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(Device).where(Device.id == id))
            device = result.scalar_one_or_none()
            if not device:
                return None
            for key, value in data.items():
                setattr(device, key, value)
            await self._maybe_commit(session)
            await session.refresh(device)
            return DeviceRead.model_validate(device)
        finally:
            await self._close_session(session)

    async def delete(self, id: Any) -> bool:
        session = await self._get_session()
        try:
            result = await session.execute(select(Device).where(Device.id == id))
            device = result.scalar_one_or_none()
            if not device:
                return False
            await session.delete(device)
            await self._maybe_commit(session)
            return True
        finally:
            await self._close_session(session)

    async def list_by_type(self, device_type: str) -> list[DeviceRead]:
        session = await self._get_session()
        try:
            result = await session.execute(select(Device).where(Device.device_type == device_type))
            return [DeviceRead.model_validate(d) for d in result.scalars().all()]
        finally:
            await self._close_session(session)

    async def list_by_user(self, user_id: uuid.UUID) -> list[DeviceRead]:
        session = await self._get_session()
        try:
            result = await session.execute(select(Device).where(Device.user_id == user_id))
            return [DeviceRead.model_validate(d) for d in result.scalars().all()]
        finally:
            await self._close_session(session)

    async def update_status(self, device_id: uuid.UUID, status: str) -> DeviceRead | None:
        return await self.update(device_id, {"status": status})

    async def heartbeat(self, device_id: uuid.UUID) -> DeviceRead | None:
        return await self.update(device_id, {"last_seen_at": datetime.now(timezone.utc)})
