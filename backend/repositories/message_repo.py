"""
Message Repository 接口与实现
处理消息的持久化和历史查询
"""

import uuid
from abc import abstractmethod
from typing import Any

from sqlalchemy import asc, delete, select

from backend.models.message import Message
from backend.schemas.message import MessageRead

from .base import AsyncBaseRepository, BaseRepository


class MessageRepository(BaseRepository):
    """Message 仓库接口"""

    @abstractmethod
    async def get_history_by_session(
        self,
        session_id: uuid.UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Any]:
        """分页获取会话的历史消息"""
        raise NotImplementedError

    @abstractmethod
    async def save_message(
        self,
        session_id: uuid.UUID,
        role: str,
        content: str,
        tool_calls: list[dict[str, Any]] | None = None,
        token_count: int | None = None,
    ) -> Any:
        """保存单条消息"""
        raise NotImplementedError

    @abstractmethod
    async def truncate_history_by_token_limit(
        self,
        session_id: uuid.UUID,
        max_tokens: int,
    ) -> list[Any]:
        """
        根据 Token 上限截断历史消息，保留最新的消息
        返回截断后的消息列表
        """
        raise NotImplementedError

    @abstractmethod
    async def get_messages_after(
        self,
        session_id: uuid.UUID,
        last_message_id: uuid.UUID,
    ) -> list[Any]:
        """获取某条消息之后的所有消息（用于断线重连增量同步）"""
        raise NotImplementedError

    @abstractmethod
    async def search_messages(
        self,
        session_id: uuid.UUID,
        query: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Any]:
        """全文搜索会话中的消息"""
        raise NotImplementedError


class AsyncMessageRepository(AsyncBaseRepository, MessageRepository):
    """基于 SQLAlchemy async session 的 Message 仓库实现"""


    async def get_by_id(self, id: Any) -> MessageRead | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(Message).where(Message.id == id))
            msg = result.scalar_one_or_none()
            return MessageRead.model_validate(msg) if msg is not None else None
        finally:
            await self._close_session(session)

    async def create(self, data: dict[str, Any]) -> MessageRead:
        session = await self._get_session()
        try:
            msg = Message(**data)
            session.add(msg)
            await self._maybe_commit(session)
            await session.refresh(msg)
            return MessageRead.model_validate(msg)
        finally:
            await self._close_session(session)

    async def update(self, id: Any, data: dict[str, Any]) -> MessageRead | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(Message).where(Message.id == id))
            msg = result.scalar_one_or_none()
            if not msg:
                return None
            for key, value in data.items():
                setattr(msg, key, value)
            await self._maybe_commit(session)
            await session.refresh(msg)
            return MessageRead.model_validate(msg)
        finally:
            await self._close_session(session)

    async def delete(self, id: Any) -> bool:
        session = await self._get_session()
        try:
            result = await session.execute(select(Message).where(Message.id == id))
            msg = result.scalar_one_or_none()
            if not msg:
                return False
            await session.delete(msg)
            await self._maybe_commit(session)
            return True
        finally:
            await self._close_session(session)

    async def get_history_by_session(
        self,
        session_id: uuid.UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> list[MessageRead]:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(Message)
                .where(Message.session_id == session_id)
                .order_by(asc(Message.created_at))
                .limit(limit)
                .offset(offset)
            )
            return [MessageRead.model_validate(m) for m in result.scalars().all()]
        finally:
            await self._close_session(session)

    async def save_message(
        self,
        session_id: uuid.UUID,
        role: str,
        content: str,
        tool_calls: list[dict[str, Any]] | None = None,
        token_count: int | None = None,
    ) -> MessageRead:
        return await self.create(
            {
                "session_id": session_id,
                "role": role,
                "content": content,
                "tool_calls": tool_calls,
                "token_count": token_count,
            }
        )

    async def truncate_history_by_token_limit(
        self,
        session_id: uuid.UUID,
        max_tokens: int,
    ) -> list[MessageRead]:
        session = await self._get_session()
        try:
            # 获取所有消息按时间正序
            result = await session.execute(
                select(Message)
                .where(Message.session_id == session_id)
                .order_by(asc(Message.created_at))
            )
            messages = list(result.scalars().all())

            total = 0
            cutoff = 0
            for i, msg in enumerate(reversed(messages)):
                total += msg.token_count or 0
                if total > max_tokens:
                    cutoff = len(messages) - i
                    break

            # 删除超出 token 限制的旧消息
            if cutoff > 0:
                to_delete = messages[:cutoff]
                ids = [m.id for m in to_delete]
                await session.execute(
                    delete(Message).where(Message.id.in_(ids))
                )
                await self._maybe_commit(session)
                return [MessageRead.model_validate(m) for m in messages[cutoff:]]
            return [MessageRead.model_validate(m) for m in messages]
        finally:
            await self._close_session(session)

    async def get_messages_after(
        self,
        session_id: uuid.UUID,
        last_message_id: uuid.UUID,
    ) -> list[MessageRead]:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(Message)
                .where(
                    Message.session_id == session_id,
                    Message.created_at > select(Message.created_at).where(Message.id == last_message_id).scalar_subquery(),
                )
                .order_by(asc(Message.created_at))
            )
            return [MessageRead.model_validate(m) for m in result.scalars().all()]
        finally:
            await self._close_session(session)

    async def search_messages(
        self,
        session_id: uuid.UUID,
        query: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[MessageRead]:
        """全文搜索会话中的消息"""
        session = await self._get_session()
        try:
            search_pattern = f"%{query}%"
            result = await session.execute(
                select(Message)
                .where(
                    Message.session_id == session_id,
                    Message.content.ilike(search_pattern),
                )
                .order_by(asc(Message.created_at))
                .limit(limit)
                .offset(offset)
            )
            return [MessageRead.model_validate(m) for m in result.scalars().all()]
        finally:
            await self._close_session(session)
