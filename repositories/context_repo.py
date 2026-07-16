"""
Context Repository 接口与实现
处理上下文项 (CtxItem) 和访问流 (ContextFlow) 的 CRUD
"""

import uuid
from abc import abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, desc, func, or_, select

from backend.models.context import ContextFlow, CtxItem, ItemKind
from backend.schemas.context import ContextFlowRead, CtxItemRead

from .base import AsyncBaseRepository, BaseRepository


class CtxItemRepository(BaseRepository):
    """CtxItem 仓库接口"""

    @abstractmethod
    async def list_by_session(
        self,
        session_id: uuid.UUID | None,
        scope: str | None = None,
        kind: str | None = None,
        search: str | None = None,
        hide_pinned: bool = False,
        limit: int = 500,
        offset: int = 0,
        user_id: uuid.UUID | None = None,
    ) -> list[Any]:
        """
        列出上下文项，支持：
        - 按 session 过滤（None 表示全局项）
        - 按 scope 过滤
        - 按 kind 过滤
        - 全文搜索（key / value / origin）
        - 隐藏 pinned 项
        """
        raise NotImplementedError

    @abstractmethod
    async def toggle_pin(
        self, item_id: uuid.UUID, pinned: bool
    ) -> Any | None:
        """切换 pinned 状态"""
        raise NotImplementedError

    @abstractmethod
    async def get_stats(
        self, session_id: uuid.UUID | None = None, user_id: uuid.UUID | None = None
    ) -> dict[str, Any]:
        """
        获取 Token 统计
        返回：total, pinned, by_scope, item_count 等
        """
        raise NotImplementedError

    @abstractmethod
    async def prune_by_ttl(
        self,
        session_id: uuid.UUID | None = None,
        ttl: str | None = None,
    ) -> int:
        """
        按 TTL 裁剪上下文项
        返回裁剪的数量
        """
        raise NotImplementedError

    @abstractmethod
    async def optimize(
        self,
        session_id: uuid.UUID | None = None,
        threshold: float = 0.7,
    ) -> dict[str, Any]:
        """
        执行上下文优化：
        1. 裁剪非 pinned 的 session message（当 usage > threshold）
        2. 将旧 doc 项合并摘要
        返回：saved_tokens, pruned_count, summarized_count
        """
        raise NotImplementedError


class ContextFlowRepository(BaseRepository):
    """ContextFlow 仓库接口"""

    @abstractmethod
    async def list_by_session(
        self,
        session_id: uuid.UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Any]:
        """分页获取会话的上下文访问流"""
        raise NotImplementedError

    @abstractmethod
    async def create_flow(
        self,
        session_id: uuid.UUID,
        agent: str,
        scope: str,
        keys: list[str],
        tokens: int,
    ) -> Any:
        """记录一次上下文访问流"""
        raise NotImplementedError

    @abstractmethod
    async def get_recent_flows(
        self,
        session_id: uuid.UUID | None = None,
        hours: int = 1,
        limit: int = 50,
    ) -> list[Any]:
        """获取最近 N 小时的访问流"""
        raise NotImplementedError


class AsyncCtxItemRepository(AsyncBaseRepository, CtxItemRepository):
    """基于 SQLAlchemy async session 的 CtxItem 仓库实现"""


    async def get_by_id(self, id: Any) -> CtxItemRead | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(CtxItem).where(CtxItem.id == id))
            item = result.scalar_one_or_none()
            return CtxItemRead.model_validate(item) if item is not None else None
        finally:
            await self._close_session(session)

    async def create(self, data: dict[str, Any]) -> CtxItemRead:
        session = await self._get_session()
        try:
            item = CtxItem(**data)
            session.add(item)
            await self._maybe_commit(session)
            await session.refresh(item)
            return CtxItemRead.model_validate(item)
        finally:
            await self._close_session(session)

    async def update(self, id: Any, data: dict[str, Any]) -> CtxItemRead | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(CtxItem).where(CtxItem.id == id))
            item = result.scalar_one_or_none()
            if not item:
                return None
            for key, value in data.items():
                setattr(item, key, value)
            await self._maybe_commit(session)
            await session.refresh(item)
            return CtxItemRead.model_validate(item)
        finally:
            await self._close_session(session)

    async def delete(self, id: Any) -> bool:
        session = await self._get_session()
        try:
            result = await session.execute(select(CtxItem).where(CtxItem.id == id))
            item = result.scalar_one_or_none()
            if not item:
                return False
            await session.delete(item)
            await self._maybe_commit(session)
            return True
        finally:
            await self._close_session(session)

    async def list_by_session(
        self,
        session_id: uuid.UUID | None,
        scope: str | None = None,
        kind: str | None = None,
        search: str | None = None,
        hide_pinned: bool = False,
        limit: int = 500,
        offset: int = 0,
        user_id: uuid.UUID | None = None,
    ) -> list[CtxItemRead]:
        session = await self._get_session()
        try:
            stmt = select(CtxItem)
            conditions = []
            if session_id is not None:
                conditions.append(CtxItem.session_id == session_id)
            else:
                conditions.append(CtxItem.session_id.is_(None))
                # 全局项：只能看到 user_id 为空（系统级）或自己的
                if user_id is not None:
                    conditions.append(
                        or_(CtxItem.user_id == user_id, CtxItem.user_id.is_(None))
                    )
            if scope is not None:
                conditions.append(CtxItem.scope == scope)
            if kind is not None:
                conditions.append(CtxItem.kind == kind)
            if search:
                like_pattern = f"%{search}%"
                conditions.append(
                    or_(
                        CtxItem.key.ilike(like_pattern),
                        CtxItem.value.ilike(like_pattern),
                        CtxItem.origin.ilike(like_pattern),
                    )
                )
            if hide_pinned:
                conditions.append(CtxItem.pinned.is_(False))
            if conditions:
                stmt = stmt.where(and_(*conditions))
            stmt = stmt.order_by(desc(CtxItem.updated_at)).limit(limit).offset(offset)
            result = await session.execute(stmt)
            return [CtxItemRead.model_validate(i) for i in result.scalars().all()]
        finally:
            await self._close_session(session)

    async def toggle_pin(self, item_id: uuid.UUID, pinned: bool) -> CtxItemRead | None:
        return await self.update(item_id, {"pinned": pinned})

    async def get_stats(self, session_id: uuid.UUID | None = None, user_id: uuid.UUID | None = None) -> dict[str, Any]:
        session = await self._get_session()
        try:
            stmt = select(CtxItem)
            if session_id is not None:
                # 会话级统计：包含全局项（session_id is NULL）+ 该会话专属项
                stmt = stmt.where(
                    or_(CtxItem.session_id == session_id, CtxItem.session_id.is_(None))
                )
            else:
                stmt = stmt.where(CtxItem.session_id.is_(None))
                if user_id is not None:
                    stmt = stmt.where(
                        or_(CtxItem.user_id == user_id, CtxItem.user_id.is_(None))
                    )

            result = await session.execute(stmt)
            items = list(result.scalars().all())

            total_tokens = sum(i.tokens or 0 for i in items)
            pinned_tokens = sum(i.tokens or 0 for i in items if i.pinned)
            session_tokens = sum(i.tokens or 0 for i in items if i.scope == "session")
            rag_tokens = sum(i.tokens or 0 for i in items if i.kind == "rag")
            by_scope: dict[str, int] = {}
            for i in items:
                by_scope[i.scope] = by_scope.get(i.scope, 0) + (i.tokens or 0)

            return {
                "total_tokens": total_tokens,
                "pinned_tokens": pinned_tokens,
                "session_tokens": session_tokens,
                "rag_tokens": rag_tokens,
                "by_scope": by_scope,
                "item_count": len(items),
            }
        finally:
            await self._close_session(session)

    async def prune_by_ttl(
        self,
        session_id: uuid.UUID | None = None,
        ttl: str | None = None,
    ) -> int:
        session = await self._get_session()
        try:
            stmt = select(CtxItem)
            conditions = [CtxItem.pinned.is_(False)]
            if session_id is not None:
                conditions.append(CtxItem.session_id == session_id)
            else:
                conditions.append(CtxItem.session_id.is_(None))
            if ttl is not None:
                conditions.append(CtxItem.ttl == ttl)
            else:
                conditions.append(CtxItem.ttl.is_not(None))
            stmt = stmt.where(and_(*conditions))
            result = await session.execute(stmt)
            to_delete = list(result.scalars().all())
            count = len(to_delete)
            for item in to_delete:
                await session.delete(item)
            await self._maybe_commit(session)
            return count
        finally:
            await self._close_session(session)

    async def optimize(
        self,
        session_id: uuid.UUID | None = None,
        threshold: float = 0.7,
    ) -> dict[str, Any]:
        session = await self._get_session()
        try:
            stats = await self.get_stats(session_id)
            total = stats.get("total_tokens", 0)
            # 简化实现：当 token 总数超过阈值时，删除最旧的非 pinned message/doc
            max_tokens = int(total * threshold) if threshold < 1.0 else int(threshold)
            if total <= max_tokens:
                return {"saved_tokens": 0, "pruned_count": 0, "summarized_count": 0}

            stmt = select(CtxItem).where(
                CtxItem.pinned.is_(False),
                CtxItem.kind.in_([ItemKind.MESSAGE, ItemKind.DOC]),
            )
            if session_id is not None:
                stmt = stmt.where(CtxItem.session_id == session_id)
            else:
                stmt = stmt.where(CtxItem.session_id.is_(None))
            stmt = stmt.order_by(CtxItem.updated_at)
            result = await session.execute(stmt)
            candidates = list(result.scalars().all())

            saved = 0
            pruned = 0
            for item in candidates:
                if total - saved <= max_tokens:
                    break
                saved += item.tokens or 0
                await session.delete(item)
                pruned += 1

            await self._maybe_commit(session)
            return {
                "saved_tokens": saved,
                "pruned_count": pruned,
                "summarized_count": 0,
            }
        finally:
            await self._close_session(session)


class AsyncContextFlowRepository(AsyncBaseRepository, ContextFlowRepository):
    """基于 SQLAlchemy async session 的 ContextFlow 仓库实现"""


    async def get_by_id(self, id: Any) -> ContextFlowRead | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(ContextFlow).where(ContextFlow.id == id))
            flow = result.scalar_one_or_none()
            return ContextFlowRead.model_validate(flow) if flow is not None else None
        finally:
            await self._close_session(session)

    async def create(self, data: dict[str, Any]) -> ContextFlowRead:
        session = await self._get_session()
        try:
            flow = ContextFlow(**data)
            session.add(flow)
            await self._maybe_commit(session)
            await session.refresh(flow)
            return ContextFlowRead.model_validate(flow)
        finally:
            await self._close_session(session)

    async def update(self, id: Any, data: dict[str, Any]) -> ContextFlowRead | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(ContextFlow).where(ContextFlow.id == id))
            flow = result.scalar_one_or_none()
            if not flow:
                return None
            for key, value in data.items():
                setattr(flow, key, value)
            await self._maybe_commit(session)
            await session.refresh(flow)
            return ContextFlowRead.model_validate(flow)
        finally:
            await self._close_session(session)

    async def delete(self, id: Any) -> bool:
        session = await self._get_session()
        try:
            result = await session.execute(select(ContextFlow).where(ContextFlow.id == id))
            flow = result.scalar_one_or_none()
            if not flow:
                return False
            await session.delete(flow)
            await self._maybe_commit(session)
            return True
        finally:
            await self._close_session(session)

    async def list_by_session(
        self,
        session_id: uuid.UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ContextFlowRead]:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(ContextFlow)
                .where(ContextFlow.session_id == session_id)
                .order_by(desc(ContextFlow.created_at))
                .limit(limit)
                .offset(offset)
            )
            return [ContextFlowRead.model_validate(f) for f in result.scalars().all()]
        finally:
            await self._close_session(session)

    async def create_flow(
        self,
        session_id: uuid.UUID,
        agent: str,
        scope: str,
        keys: list[str],
        tokens: int,
    ) -> ContextFlowRead:
        return await self.create(
            {
                "session_id": session_id,
                "agent": agent,
                "scope": scope,
                "keys": keys,
                "tokens": tokens,
            }
        )

    async def get_recent_flows(
        self,
        session_id: uuid.UUID | None = None,
        hours: int = 1,
        limit: int = 50,
    ) -> list[ContextFlowRead]:
        session = await self._get_session()
        try:
            since = datetime.now(timezone.utc) - timedelta(hours=hours)
            stmt = select(ContextFlow).where(ContextFlow.created_at >= since)
            if session_id is not None:
                stmt = stmt.where(ContextFlow.session_id == session_id)
            stmt = stmt.order_by(desc(ContextFlow.created_at)).limit(limit)
            result = await session.execute(stmt)
            return [ContextFlowRead.model_validate(f) for f in result.scalars().all()]
        finally:
            await self._close_session(session)
