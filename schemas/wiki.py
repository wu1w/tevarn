"""
Wiki Graph 相关 Pydantic Schema
"""

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class WikiEntityBase(BaseModel):
    name: str = Field(..., max_length=128)
    entity_type: str = Field(..., max_length=32)
    description: str = ""
    aliases: list[str] = Field(default_factory=list)
    # 注意：字段名为 `meta` 而非 `metadata`，因为 SQLAlchemy 声明式模型实例
    # 自带 `metadata` 类属性（指向 Base.metadata），如果 Schema 字段也叫
    # `metadata`，从 ORM 对象序列化时会读到错误的 MetaData 对象而不是真实数据。
    meta: dict[str, Any] = Field(default_factory=dict)


class WikiEntityCreate(WikiEntityBase):
    pass


class WikiEntityUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=128)
    entity_type: Optional[str] = Field(None, max_length=32)
    description: Optional[str] = None
    aliases: Optional[list[str]] = None
    meta: Optional[dict[str, Any]] = None


class WikiEntityRead(WikiEntityBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class WikiRelationBase(BaseModel):
    source_id: uuid.UUID
    target_id: uuid.UUID
    relation_type: str = Field(..., max_length=32)
    weight: float = 1.0
    evidence: str = ""


class WikiRelationCreate(WikiRelationBase):
    pass


class WikiRelationRead(WikiRelationBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class WikiImportSource(str, Enum):
    text = "text"
    json = "json"
    context = "context"


class WikiImportRequest(BaseModel):
    source: WikiImportSource
    content: str | None = Field(None, description="原始文本 / JSON 字符串")
    session_id: uuid.UUID | None = Field(None, description="source=context 时可选指定会话")
    options: dict[str, Any] = Field(default_factory=dict, description="覆盖同名实体、自定义提示词等选项")


class WikiImportResult(BaseModel):
    entities_created: int = 0
    entities_updated: int = 0
    relations_created: int = 0
    skipped: int = 0
    detail: list[str] = Field(default_factory=list)
