"""
WebSocket 报文格式 Schema
"""

import uuid
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
    # 结构化可观测字段（避免前端解析文案正则）
    caps_count: Optional[int] = None
    tools_count: Optional[int] = None


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


class ConfirmRequest(WSMessage):
    """危险操作 / clarify 确认请求（服务端 → 前端弹窗）。

    与 confirm_manager.request_confirmation 广播载荷对齐。
    kind=clarify 时 options 为可点选项。
    """

    type: Literal["confirm_request"] = "confirm_request"
    confirm_id: str
    title: str
    command: str
    reason: str = ""
    timeout: Optional[float] = None
    tool: str = ""
    agent_id: str = ""
    agent_name: str = ""
    user_id: str = ""
    scopes: list[str] = Field(
        default_factory=lambda: ["once", "session", "agent", "deny"]
    )
    kind: str = "danger"  # danger | clarify
    options: list[str] = Field(default_factory=list)


class ConfirmResponse(WSMessage):
    """危险操作 / clarify 确认响应（前端 → 服务端）"""

    type: Literal["confirm_response"] = "confirm_response"
    confirm_id: str
    approved: bool
    scope: Optional[str] = None  # once | session | agent | deny
    choice: Optional[str] = None  # clarify 选项原文


class ScreenshotEvent(WSMessage):
    """实时截图推送（desktop_screenshot / browser screenshot / computer_use capture）"""

    type: Literal["screenshot"] = "screenshot"
    image_base64: str = ""  # data:image/...;base64,... 或纯 base64（可空，改用 image_url）
    image_url: str = ""  # 优先：可直接 <img src> 的 URL
    tool_name: str = ""  # 触发截图的工具名
    timestamp: str = ""  # ISO 8601
