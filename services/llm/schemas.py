"""
LLM 服务相关的 Pydantic Schema
"""

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    """LLM 工具调用"""

    id: str
    name: str
    arguments: dict[str, Any]


class LLMResponse(BaseModel):
    """LLM 完整响应"""

    message_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    role: Literal["assistant"] = "assistant"
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: Literal["stop", "tool_calls", "length", "error"] = "stop"
    usage: dict[str, int] = Field(default_factory=dict)

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


class LLMChunk(BaseModel):
    """LLM 流式输出块"""

    message_id: uuid.UUID
    delta: str = ""  # 文本增量
    tool_call: ToolCall | None = None  # 工具调用增量
    finish_reason: Literal["stop", "tool_calls", "length", "error"] | None = None
