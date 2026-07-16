"""
Context 相关的 Pydantic Schema
对应前端 demo 中的 CtxItem 和 Flow 类型
"""

import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

ScopeKey = Literal["system", "user", "project", "session", "knowledge"]
ItemKind = Literal["instruction", "memory", "doc", "message", "rag", "tool-def"]


class CtxItemBase(BaseModel):
    """CtxItem 基础字段"""

    scope: ScopeKey
    kind: ItemKind = "memory"
    key: str = Field(..., max_length=256)
    value: str = ""
    tokens: int = 0
    pinned: bool = False
    ttl: Optional[str] = None  # "1h" / "24h" / "session"
    origin: Optional[str] = None


class CtxItemCreate(CtxItemBase):
    """创建上下文项请求"""

    session_id: Optional[uuid.UUID] = None


class CtxItemUpdate(BaseModel):
    """更新上下文项请求"""

    key: Optional[str] = Field(None, max_length=256)
    value: Optional[str] = None
    tokens: Optional[int] = None
    pinned: Optional[bool] = None
    ttl: Optional[str] = None
    origin: Optional[str] = None


class CtxItemRead(CtxItemBase):
    """上下文项响应"""

    id: uuid.UUID
    session_id: Optional[uuid.UUID]
    updated_at: datetime

    model_config = {"from_attributes": True}


class CtxItemPinToggle(BaseModel):
    """切换 pinned 状态"""

    pinned: bool


class ContextFlowCreate(BaseModel):
    """创建上下文流记录"""

    session_id: uuid.UUID
    agent: str
    scope: ScopeKey
    keys: list[str] = Field(default_factory=list)
    tokens: int = 0


class ContextFlowRead(BaseModel):
    """上下文流响应"""

    id: uuid.UUID
    session_id: uuid.UUID
    agent: str
    scope: ScopeKey
    keys: list[str]
    tokens: int
    created_at: datetime

    model_config = {"from_attributes": True}


class ContextStats(BaseModel):
    """Token 统计响应"""

    total_tokens: int
    pinned_tokens: int
    session_tokens: int
    rag_tokens: int
    by_scope: dict[str, int]
    item_count: int
    context_window: int = 200_000  # 默认 200K


class ContextOptimizeResult(BaseModel):
    """优化结果"""

    saved_tokens: int
    pruned_count: int
    summarized_count: int


class ContextSearchQuery(BaseModel):
    """搜索查询参数"""

    q: Optional[str] = None
    scope: Optional[ScopeKey] = None
    hide_pinned: bool = False
    kind: Optional[ItemKind] = None
