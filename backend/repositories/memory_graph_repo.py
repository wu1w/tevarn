"""MemoryNode / MemoryEdge 仓库（Phase 1 Memory Graph MVP）

文本召回用 LIKE 匹配（本地 MVP 口径；向量检索后续叠加到 recall 排序）。
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, desc, or_

from backend.models.memory_graph import MemoryEdge, MemoryNode
from backend.repositories.base import AsyncBaseRepository

VALID_KINDS = ("knowledge", "decision", "preference", "experience")
VALID_RELATIONS = ("related_to", "part_of", "supports", "contradicts", "derived_from")


class AsyncMemoryGraphRepository(AsyncBaseRepository):
    """记忆图存取"""

    # ─────────── Node ───────────

    async def add_node(self, data: dict[str, Any]) -> MemoryNode:
        session = await self._get_session()
        try:
            obj = MemoryNode(**data)
            session.add(obj)
            await self._maybe_commit(session)
            await session.refresh(obj)
            return obj
        except Exception:
            await session.rollback()
            raise
        finally:
            await self._close_session(session)

    async def get_node(self, node_id: uuid.UUID) -> MemoryNode | None:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(MemoryNode).where(MemoryNode.id == node_id)
            )
            return result.scalar_one_or_none()
        finally:
            await self._close_session(session)

    async def update_node(self, node_id: uuid.UUID, data: dict[str, Any]) -> MemoryNode | None:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(MemoryNode).where(MemoryNode.id == node_id)
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

    async def delete_node(self, node_id: uuid.UUID) -> bool:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(MemoryNode).where(MemoryNode.id == node_id)
            )
            obj = result.scalar_one_or_none()
            if obj is None:
                return False
            # 显式删边（不依赖 DB 层 FK CASCADE，sqlite 默认不开外键）
            from sqlalchemy import delete as sa_delete

            await session.execute(
                sa_delete(MemoryEdge).where(
                    or_(MemoryEdge.from_id == node_id, MemoryEdge.to_id == node_id)
                )
            )
            await session.delete(obj)
            await self._maybe_commit(session)
            return True
        except Exception:
            await session.rollback()
            raise
        finally:
            await self._close_session(session)

    async def recall(
        self,
        *,
        query: str = "",
        kind: str | None = None,
        limit: int = 10,
        bump_hits: bool = True,
    ) -> list[MemoryNode]:
        """文本召回：title/content LIKE + kind 过滤；按 hit_count+置信度+新鲜度排序"""
        session = await self._get_session()
        try:
            stmt = select(MemoryNode)
            if kind:
                stmt = stmt.where(MemoryNode.kind == kind)
            q = (query or "").strip()
            if q:
                like = f"%{q}%"
                stmt = stmt.where(
                    or_(
                        MemoryNode.title.like(like),
                        MemoryNode.content.like(like),
                    )
                )
            stmt = stmt.order_by(
                desc(MemoryNode.hit_count),
                desc(MemoryNode.confidence),
                desc(MemoryNode.updated_at),
            ).limit(limit)
            result = await session.execute(stmt)
            nodes = list(result.scalars().all())
            if bump_hits and nodes:
                for n in nodes:
                    n.hit_count = (n.hit_count or 0) + 1
                await self._maybe_commit(session)
            return nodes
        except Exception:
            await session.rollback()
            raise
        finally:
            await self._close_session(session)

    async def count_nodes(self) -> int:
        from sqlalchemy import func

        session = await self._get_session()
        try:
            result = await session.execute(select(func.count(MemoryNode.id)))
            return int(result.scalar() or 0)
        finally:
            await self._close_session(session)

    # ─────────── Edge ───────────

    async def add_edge(self, data: dict[str, Any]) -> MemoryEdge:
        session = await self._get_session()
        try:
            obj = MemoryEdge(**data)
            session.add(obj)
            await self._maybe_commit(session)
            await session.refresh(obj)
            return obj
        except Exception:
            await session.rollback()
            raise
        finally:
            await self._close_session(session)

    async def edges_of(self, node_id: uuid.UUID) -> list[MemoryEdge]:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(MemoryEdge).where(
                    or_(MemoryEdge.from_id == node_id, MemoryEdge.to_id == node_id)
                )
            )
            return list(result.scalars().all())
        finally:
            await self._close_session(session)
