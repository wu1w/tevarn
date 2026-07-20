"""
DesktopPermission Repository 接口与实现
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.desktop_permission import DesktopPermission
from backend.repositories.base import AsyncBaseRepository, BaseRepository


class DesktopPermissionRepository(BaseRepository):
    """DesktopPermission 仓库接口"""

    async def get_permission(
        self, 
        user_id: uuid.UUID, 
        operation: str, 
        app_name: str | None = None
    ) -> DesktopPermission | None:
        """获取权限"""
        raise NotImplementedError

    async def save_permission(
        self,
        user_id: uuid.UUID,
        operation: str,
        app_name: str | None,
        level: str,
    ) -> DesktopPermission:
        """保存权限"""
        raise NotImplementedError

    async def delete_permission(
        self,
        user_id: uuid.UUID,
        operation: str,
        app_name: str | None = None,
    ) -> bool:
        """删除权限"""
        raise NotImplementedError


class AsyncDesktopPermissionRepository(AsyncBaseRepository, DesktopPermissionRepository):
    """DesktopPermission 异步仓库实现"""

    async def get_by_id(self, id: Any) -> DesktopPermission | None:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(DesktopPermission).where(DesktopPermission.id == id)
            )
            return result.scalar_one_or_none()
        finally:
            await self._close_session(session)

    async def get_by_id_for_user(self, id: Any, user_id: Any) -> DesktopPermission | None:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(DesktopPermission).where(
                    DesktopPermission.id == id,
                    DesktopPermission.user_id == user_id,
                )
            )
            return result.scalar_one_or_none()
        finally:
            await self._close_session(session)

    async def create(self, data: dict[str, Any]) -> DesktopPermission:
        session = await self._get_session()
        try:
            obj = DesktopPermission(**data)
            session.add(obj)
            await self._maybe_commit(session)
            await session.refresh(obj)
            return obj
        finally:
            await self._close_session(session)

    async def update(self, id: Any, data: dict[str, Any]) -> DesktopPermission | None:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(DesktopPermission).where(DesktopPermission.id == id)
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
        session = await self._get_session()
        try:
            result = await session.execute(
                select(DesktopPermission).where(DesktopPermission.id == id)
            )
            obj = result.scalar_one_or_none()
            if not obj:
                return False
            await session.delete(obj)
            await self._maybe_commit(session)
            return True
        finally:
            await self._close_session(session)

    async def get_permission(
        self, 
        user_id: uuid.UUID, 
        operation: str, 
        app_name: str | None = None
    ) -> DesktopPermission | None:
        session = await self._get_session()
        try:
            query = select(DesktopPermission).where(
                DesktopPermission.user_id == user_id,
                DesktopPermission.operation == operation,
            )
            if app_name:
                query = query.where(DesktopPermission.app_name == app_name)
            else:
                query = query.where(DesktopPermission.app_name.is_(None))
            
            result = await session.execute(query)
            return result.scalar_one_or_none()
        finally:
            await self._close_session(session)

    async def save_permission(
        self,
        user_id: uuid.UUID,
        operation: str,
        app_name: str | None,
        level: str,
    ) -> DesktopPermission:
        # 先查找是否已存在
        existing = await self.get_permission(user_id, operation, app_name)
        
        if existing:
            # 更新
            return await self.update(existing.id, {"level": level})
        else:
            # 创建
            return await self.create({
                "user_id": user_id,
                "operation": operation,
                "app_name": app_name,
                "level": level,
            })

    async def delete_permission(
        self,
        user_id: uuid.UUID,
        operation: str,
        app_name: str | None = None,
    ) -> bool:
        existing = await self.get_permission(user_id, operation, app_name)
        if existing:
            return await self.delete(existing.id)
        return False
