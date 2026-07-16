"""
Knowledge Repository 接口与实现
"""

import uuid
from abc import abstractmethod
from typing import Any

from sqlalchemy import or_, select, update

from backend.models.knowledge import Chunk, Document

from .base import AsyncBaseRepository, BaseRepository


class DocumentRepository(BaseRepository):
    """Document 仓库接口"""

    @abstractmethod
    async def list_all(self) -> list[Any]:
        """列出所有文档"""
        raise NotImplementedError

    @abstractmethod
    async def list_by_status(self, status: str) -> list[Any]:
        """按状态列出文档"""
        raise NotImplementedError

    @abstractmethod
    async def list_by_user(self, user_id: uuid.UUID) -> list[Any]:
        """列出当前用户可见的文档（含全局共享）"""
        raise NotImplementedError

    @abstractmethod
    async def update_status(
        self, doc_id: uuid.UUID, status: str
    ) -> Any | None:
        """更新文档处理状态"""
        raise NotImplementedError

    @abstractmethod
    async def increment_chunks(self, doc_id: uuid.UUID, count: int = 1) -> Any | None:
        """增加文档分块计数"""
        raise NotImplementedError


class ChunkRepository(BaseRepository):
    """Chunk 仓库接口"""

    @abstractmethod
    async def list_by_document(self, doc_id: uuid.UUID) -> list[Any]:
        """列出文档的所有分块"""
        raise NotImplementedError

    @abstractmethod
    async def update_vector_id(
        self, chunk_id: uuid.UUID, vector_id: str
    ) -> Any | None:
        """更新向量数据库 ID"""
        raise NotImplementedError


class AsyncDocumentRepository(AsyncBaseRepository, DocumentRepository):
    """基于 SQLAlchemy async session 的 Document 仓库实现"""


    async def get_by_id(self, id: Any) -> Document | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(Document).where(Document.id == id))
            return result.scalar_one_or_none()
        finally:
            await self._close_session(session)

    async def create(self, data: dict[str, Any]) -> Document:
        session = await self._get_session()
        try:
            doc = Document(**data)
            session.add(doc)
            await self._maybe_commit(session)
            await session.refresh(doc)
            return doc
        finally:
            await self._close_session(session)

    async def update(self, id: Any, data: dict[str, Any]) -> Document | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(Document).where(Document.id == id))
            doc = result.scalar_one_or_none()
            if not doc:
                return None
            for key, value in data.items():
                setattr(doc, key, value)
            await self._maybe_commit(session)
            await session.refresh(doc)
            return doc
        finally:
            await self._close_session(session)

    async def delete(self, id: Any) -> bool:
        session = await self._get_session()
        try:
            result = await session.execute(select(Document).where(Document.id == id))
            doc = result.scalar_one_or_none()
            if not doc:
                return False
            await session.delete(doc)
            await self._maybe_commit(session)
            return True
        finally:
            await self._close_session(session)

    async def list_all(self) -> list[Document]:
        session = await self._get_session()
        try:
            result = await session.execute(select(Document).order_by(Document.created_at.desc()))
            return list(result.scalars().all())
        finally:
            await self._close_session(session)

    async def list_by_user(self, user_id: uuid.UUID) -> list[Document]:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(Document)
                .where(
                    or_(
                        Document.user_id == user_id,
                        Document.user_id.is_(None),
                    )
                )
                .order_by(Document.created_at.desc())
            )
            return list(result.scalars().all())
        finally:
            await self._close_session(session)

    async def list_by_status(self, status: str) -> list[Document]:
        session = await self._get_session()
        try:
            result = await session.execute(select(Document).where(Document.status == status))
            return list(result.scalars().all())
        finally:
            await self._close_session(session)

    async def update_status(self, doc_id: uuid.UUID, status: str) -> Document | None:
        return await self.update(doc_id, {"status": status})

    async def increment_chunks(self, doc_id: uuid.UUID, count: int = 1) -> Document | None:
        session = await self._get_session()
        try:
            # 使用原子表达式更新，避免并发写入丢失计数
            await session.execute(
                update(Document)
                .where(Document.id == doc_id)
                .values(chunks_count=Document.chunks_count + count)
            )
            await self._maybe_commit(session)
            result = await session.execute(select(Document).where(Document.id == doc_id))
            return result.scalar_one_or_none()
        finally:
            await self._close_session(session)


class AsyncChunkRepository(AsyncBaseRepository, ChunkRepository):
    """基于 SQLAlchemy async session 的 Chunk 仓库实现"""


    async def get_by_id(self, id: Any) -> Chunk | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(Chunk).where(Chunk.id == id))
            return result.scalar_one_or_none()
        finally:
            await self._close_session(session)

    async def create(self, data: dict[str, Any]) -> Chunk:
        session = await self._get_session()
        try:
            chunk = Chunk(**data)
            session.add(chunk)
            await self._maybe_commit(session)
            await session.refresh(chunk)
            return chunk
        finally:
            await self._close_session(session)

    async def update(self, id: Any, data: dict[str, Any]) -> Chunk | None:
        session = await self._get_session()
        try:
            result = await session.execute(select(Chunk).where(Chunk.id == id))
            chunk = result.scalar_one_or_none()
            if not chunk:
                return None
            for key, value in data.items():
                setattr(chunk, key, value)
            await self._maybe_commit(session)
            await session.refresh(chunk)
            return chunk
        finally:
            await self._close_session(session)

    async def delete(self, id: Any) -> bool:
        session = await self._get_session()
        try:
            result = await session.execute(select(Chunk).where(Chunk.id == id))
            chunk = result.scalar_one_or_none()
            if not chunk:
                return False
            await session.delete(chunk)
            await self._maybe_commit(session)
            return True
        finally:
            await self._close_session(session)

    async def list_by_document(self, doc_id: uuid.UUID) -> list[Chunk]:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(Chunk).where(Chunk.document_id == doc_id).order_by(Chunk.index)
            )
            return list(result.scalars().all())
        finally:
            await self._close_session(session)

    async def delete_by_document(self, doc_id: uuid.UUID) -> int:
        """Remove all chunks for a document (before re-index)."""
        session = await self._get_session()
        try:
            result = await session.execute(select(Chunk).where(Chunk.document_id == doc_id))
            rows = list(result.scalars().all())
            for ch in rows:
                await session.delete(ch)
            await self._maybe_commit(session)
            return len(rows)
        finally:
            await self._close_session(session)

    async def update_vector_id(self, chunk_id: uuid.UUID, vector_id: str) -> Chunk | None:
        return await self.update(chunk_id, {"vector_id": vector_id})
