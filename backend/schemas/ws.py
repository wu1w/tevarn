"""
WebSocket 报文格式 Schema
"""

import uuid
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class WSMessage(BaseModel):
    """WebSocket 通用报文基类"""

    type: str
    session_id: Optional[uuid.UUID] = None


class StreamDelta(WSMessage):
    """流式文本输出增量"""

    type: Literal["stream_delta"] = "stream_delta"
    message_id: uuid.UUID
    content: str


class StatusUpdate(WSMessage):
    """状态更新（用于任务看板）"""

    type: Literal["status"] = "status"
    state: str  # idle / thinking / tool_executing
    detail: Optional[str] = None


class MemoryUpdated(WSMessage):
    """长期记忆自动更新通知"""

    type: Literal["memory_updated"] = "memory_updated"
    diff: str


class TaskUpdate(WSMessage):
    """任务进度更新"""

    type: Literal["task_update"] = "task_update"
    task_id: uuid.UUID
    name: str
    status: str
    progress: int = Field(..., ge=0, le=100)
    log: Optional[str] = None


class UserInput(WSMessage):
    """用户输入消息"""

    type: Literal["user_input"] = "user_input"
    content: str
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    mode: Literal["default", "deepthink", "search", "ppt", "report", "goal"] = "default"


class GoalUpdate(WSMessage):
    """Goal 模式 todo / 进度推送"""

    type: Literal["goal_update"] = "goal_update"
    goal: Optional[dict[str, Any]] = None


class ToolEvent(WSMessage):
    """工具调用实时事件（开始/结束），供前端边跑边展示"""

    type: Literal["tool_event"] = "tool_event"
    phase: Literal["start", "end"] = "start"
    tool_call_id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: Literal["running", "completed", "failed"] = "running"
    result: Optional[str] = None


class SyncRequest(WSMessage):
    """断线重连同步请求"""

    type: Literal["sync"] = "sync"
    last_message_id: Optional[uuid.UUID] = None
