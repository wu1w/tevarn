"""Session Trace 模型 — 透明化 Agent

记录每次 Agent 运行的思考链、工具调用链、RAG 溯源。
独立于 messages 表，不影响现有数据结构。
"""

import uuid
from typing import Any, Optional

from sqlalchemy import ForeignKey, Integer, String, Text, JSON, Float
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin


class SessionTrace(Base, UUIDMixin, TimestampMixin):
    """每次 Agent 运行的完整轨迹记录"""

    __tablename__ = "session_traces"

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # 关联的 assistant message id（可追溯）
    message_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        String(36), nullable=True, index=True
    )

    # 思考链 — 每次迭代的推理内容
    thinking_steps: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list
    )
    # 格式: [{"iteration": 1, "content": "分析用户意图...", "duration_ms": 1200}]

    # 工具调用链
    tool_calls_trace: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list
    )
    # 格式: [{"name": "read_file", "arguments": {...}, "result_summary": "...", "status": "completed", "duration_ms": 350}]

    # RAG 溯源
    rag_sources: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list
    )
    # 格式: [{"title": "V0.2_DESIGN.md", "collection": "knowledge", "score": 0.92, "text_preview": "..."}]

    # 集群执行信息（如果是集群模式）
    cluster_info: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON, nullable=True
    )

    # 统计
    total_iterations: Mapped[int] = mapped_column(Integer, default=0)
    total_tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)

    # 用户输入摘要
    user_input_summary: Mapped[str] = mapped_column(String(512), default="")

    # 状态
    status: Mapped[str] = mapped_column(
        String(20), default="completed"
    )  # running, completed, failed, stopped
