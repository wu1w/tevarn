"""
Knowledge 相关 Pydantic Schema
"""

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class DocumentBase(BaseModel):
    title: str = Field(..., max_length=256)
    source: str = ""
    status: str = Field(default="pending", max_length=16)
    chunks_count: int = 0
    # 注意：字段名为 `meta` 而非 `metadata`，因为 SQLAlchemy 声明式模型实例
    # 自带 `metadata` 类属性（指向 Base.metadata），如果 Schema 字段也叫
    # `metadata`，从 ORM 对象序列化时会读到错误的 MetaData 对象而不是真实数据。
    meta: dict[str, Any] = Field(default_factory=dict)


class DocumentCreate(DocumentBase):
    pass


class DocumentUpdate(BaseModel):
    title: Optional[str] = Field(None, max_length=256)
    status: Optional[str] = Field(None, max_length=16)
    meta: Optional[dict[str, Any]] = None


class DocumentRead(DocumentBase):
    id: uuid.UUID
    user_id: Optional[uuid.UUID] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ChunkBase(BaseModel):
    document_id: uuid.UUID
    content: str
    index: int = 0
    vector_id: Optional[str] = Field(None, max_length=64)
    meta: dict[str, Any] = Field(default_factory=dict)


class ChunkRead(ChunkBase):
    id: uuid.UUID
    created_at: datetime

    model_config = {"from_attributes": True}
