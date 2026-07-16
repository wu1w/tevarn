"""
Tool Repository 接口与实现
处理工具的 CRUD 和启用状态管理
"""

import uuid
from abc import abstractmethod
from typing import Any

from sqlalchemy import select

from backend.models.tool import Tool

from .base import AsyncBaseRepository, BaseRepository


class ToolRepository(BaseRepository):
    """Tool 仓库接口"""

    @abstractmethod
    async def get_active_tools(self) -> list[Any]:
        """获取所有已启用的工具"""
        raise NotImplementedError

    @abstractmethod
    async def get_tool_by_name(self, name: str) -> Any | None:
        """根据名称获取工具"""
        raise NotImplementedError

    @abstractmethod
    async def toggle_tool(
        self, tool_id: uuid.UUID, enabled: bool
    ) -> Any | None:
        """切换工具启用状态"""
        raise NotImplementedError

    @abstractmethod
    async def get_tools_by_type(self, tool_type: str) -> list[Any]:
        """根据类型获取工具"""
        raise NotImplementedError


class AsyncToolRepository(AsyncBaseRepository, ToolRepository):
    """基于 SQLAlchemy async session 的 Tool 仓库实现"""


    async def get_by_id(self, id: Any) -> Tool | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(Tool).where(Tool.id == id))
            return result.scalar_one_or_none()
        finally:
            await self._close_session(session)

    async def create(self, data: dict[str, Any]) -> Tool:
        session = await self._get_session()
        try:
            tool = Tool(**data)
            session.add(tool)
            await self._maybe_commit(session)
            await session.refresh(tool)
            return tool
        finally:
            await self._close_session(session)

    async def update(self, id: Any, data: dict[str, Any]) -> Tool | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(Tool).where(Tool.id == id))
            tool = result.scalar_one_or_none()
            if not tool:
                return None
            for key, value in data.items():
                setattr(tool, key, value)
            await self._maybe_commit(session)
            await session.refresh(tool)
            return tool
        finally:
            await self._close_session(session)

    async def delete(self, id: Any) -> bool:
        session = await self._get_session()
        try:
            result = await session.execute(select(Tool).where(Tool.id == id))
            tool = result.scalar_one_or_none()
            if not tool:
                return False
            if tool.is_builtin:
                return False
            await session.delete(tool)
            await self._maybe_commit(session)
            return True
        finally:
            await self._close_session(session)

    async def get_active_tools(self) -> list[Tool]:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(Tool).where(Tool.enabled.is_(True))
            )
            return list(result.scalars().all())
        finally:
            await self._close_session(session)

    async def get_tool_by_name(self, name: str) -> Tool | None:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(Tool).where(Tool.name == name)
            )
            return result.scalar_one_or_none()
        finally:
            await self._close_session(session)

    async def toggle_tool(self, tool_id: uuid.UUID, enabled: bool) -> Tool | None:
        return await self.update(tool_id, {"enabled": enabled})

    async def get_tools_by_type(self, tool_type: str) -> list[Tool]:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(Tool).where(Tool.type == tool_type)
            )
            return list(result.scalars().all())
        finally:
            await self._close_session(session)

    async def list_all(self) -> list[Tool]:
        """获取所有工具（不分页）"""
        session = await self._get_session()
        try:
            result = await session.execute(select(Tool).order_by(Tool.name))
            return list(result.scalars().all())
        finally:
            await self._close_session(session)
