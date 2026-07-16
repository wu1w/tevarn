"""
Setting Repository 接口与实现
"""

from abc import abstractmethod
from typing import Any

from sqlalchemy import select

from backend.core.encryption import decrypt_setting, encrypt_setting
from backend.models.setting import Setting

from .base import AsyncBaseRepository, BaseRepository


class SettingRepository(BaseRepository):
    """Setting 仓库接口"""

    @abstractmethod
    async def get_by_key(self, key: str) -> Any | None:
        """按 key 获取配置"""
        raise NotImplementedError

    @abstractmethod
    async def list_by_category(self, category: str) -> list[Any]:
        """按分类列出配置"""
        raise NotImplementedError

    @abstractmethod
    async def list_all(self) -> list[Any]:
        """列出所有配置"""
        raise NotImplementedError

    @abstractmethod
    async def upsert(self, key: str, value: Any, category: str = "general", description: str | None = None) -> Any:
        """创建或更新配置"""
        raise NotImplementedError


class AsyncSettingRepository(AsyncBaseRepository, SettingRepository):
    """基于 SQLAlchemy async session 的 Setting 仓库实现"""


    async def get_by_id(self, id: Any) -> Setting | None:
        # Setting 主键是字符串 key
        return await self.get_by_key(str(id))

    async def create(self, data: dict[str, Any]) -> Setting:
        session = await self._get_session()
        try:
            setting = Setting(**data)
            session.add(setting)
            await self._maybe_commit(session)
            await session.refresh(setting)
            return setting
        finally:
            await self._close_session(session)

    async def update(self, id: Any, data: dict[str, Any]) -> Setting | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(Setting).where(Setting.key == str(id)))
            setting = result.scalar_one_or_none()
            if not setting:
                return None
            for key, value in data.items():
                setattr(setting, key, value)
            await self._maybe_commit(session)
            await session.refresh(setting)
            return setting
        finally:
            await self._close_session(session)

    async def delete(self, id: Any) -> bool:
        session = await self._get_session()
        try:
            result = await session.execute(select(Setting).where(Setting.key == str(id)))
            setting = result.scalar_one_or_none()
            if not setting:
                return False
            await session.delete(setting)
            await self._maybe_commit(session)
            return True
        finally:
            await self._close_session(session)

    async def get_by_key(self, key: str) -> Setting | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(Setting).where(Setting.key == key))
            setting = result.scalar_one_or_none()
            if setting is not None:
                setting.value = decrypt_setting(setting.value, key=setting.key)
            return setting
        finally:
            await self._close_session(session)

    async def list_by_category(self, category: str) -> list[Setting]:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(Setting).where(Setting.category == category)
            )
            settings = list(result.scalars().all())
            for s in settings:
                s.value = decrypt_setting(s.value, key=s.key)
            return settings
        finally:
            await self._close_session(session)

    async def list_all(self) -> list[Setting]:
        session = await self._get_session()
        try:
            result = await session.execute(select(Setting))
            settings = list(result.scalars().all())
            for s in settings:
                s.value = decrypt_setting(s.value, key=s.key)
            return settings
        finally:
            await self._close_session(session)

    async def upsert(
        self,
        key: str,
        value: Any,
        category: str = "general",
        description: str | None = None,
    ) -> Setting:
        session = await self._get_session()
        try:
            result = await session.execute(select(Setting).where(Setting.key == key))
            setting = result.scalar_one_or_none()
            encrypted_value = encrypt_setting(value, key=key)
            if setting:
                setting.value = encrypted_value
                setting.category = category
                if description is not None:
                    setting.description = description
            else:
                setting = Setting(
                    key=key,
                    value=encrypted_value,
                    category=category,
                    description=description,
                )
                session.add(setting)
            await self._maybe_commit(session)
            await session.refresh(setting)
            # 返回解密后的明文，供路由应用运行时配置；响应序列化时再脱敏
            setting.value = decrypt_setting(setting.value, key=setting.key)
            return setting
        finally:
            await self._close_session(session)
