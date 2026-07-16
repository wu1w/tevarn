"""
MCP Server 数据仓库
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.mcp_server import MCPServer
from backend.schemas.mcp import MCPServerCreate, MCPServerUpdate


class MCPServerRepository:
    """MCP Server 数据库访问（异步风格接口）"""

    def __init__(self, db: AsyncSession | None = None):
        self.db = db

    async def list_all(self) -> list[MCPServer]:
        if self.db is not None:
            result = await self.db.execute(select(MCPServer).order_by(MCPServer.name))
            return list(result.scalars().all())
        from backend.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(MCPServer).order_by(MCPServer.name))
            return list(result.scalars().all())

    async def get_by_id(self, server_id: uuid.UUID) -> MCPServer | None:
        if self.db is not None:
            return await self.db.get(MCPServer, server_id)
        from backend.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            return await session.get(MCPServer, server_id)

    async def get_by_name(self, name: str) -> MCPServer | None:
        if self.db is not None:
            result = await self.db.execute(
                select(MCPServer).where(MCPServer.name == name)
            )
            return result.scalar_one_or_none()
        from backend.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(MCPServer).where(MCPServer.name == name)
            )
            return result.scalar_one_or_none()

    async def create(self, data: MCPServerCreate) -> MCPServer:
        from backend.database import AsyncSessionLocal
        server = MCPServer(**data.model_dump())
        async with AsyncSessionLocal() as session:
            session.add(server)
            await session.commit()
            await session.refresh(server)
            return server

    async def update(
        self, server_id: uuid.UUID, data: MCPServerUpdate
    ) -> MCPServer | None:
        server = await self.get_by_id(server_id)
        if server is None:
            return None
        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(server, key, value)
        from backend.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            merged = await session.merge(server)
            await session.commit()
            await session.refresh(merged)
            return merged

    async def delete(self, server_id: uuid.UUID) -> bool:
        server = await self.get_by_id(server_id)
        if server is None:
            return False
        from backend.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            await session.delete(server)
            await session.commit()
            return True

    async def toggle(self, server_id: uuid.UUID, enabled: bool) -> MCPServer | None:
        server = await self.get_by_id(server_id)
        if server is None:
            return None
        server.enabled = enabled
        from backend.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            merged = await session.merge(server)
            await session.commit()
            await session.refresh(merged)
            return merged


class AsyncMCPServerRepository(MCPServerRepository):
    """MCP Server 异步仓库（与项目现有 AsyncXxxRepository 风格一致）"""

    def __init__(self, db: AsyncSession | None = None):
        super().__init__(db)

    async def get_all_enabled(self) -> list[MCPServer]:
        if self.db is not None:
            result = await self.db.execute(
                select(MCPServer).where(MCPServer.enabled.is_(True))
            )
            return list(result.scalars().all())
        from backend.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(MCPServer).where(MCPServer.enabled.is_(True))
            )
            return list(result.scalars().all())
