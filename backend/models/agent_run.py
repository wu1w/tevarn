"""Agent Run / RunStep 模型（Phase 0.5.2 Durable Execution）

- AgentRun：一次 NexusAgentLoop.run() 调用的持久化记录（状态机见 agent/run_state.py）
- RunStep：run 内的步骤流水（phase 迁移 / 工具调用 / 备注），按 seq 有序
与 SessionTrace 的区别：Trace 是「事后复盘报告」，Run/Step 是「运行期一等公民」，
支撑 checkpoint/resume 接 run_id 与后续 Run Timeline 观测台。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin


class AgentRun(Base, UUIDMixin, TimestampMixin):
    """一次 Agent 运行的持久化记录（Phase 2 对外即 Run；表名 agent_runs 保留）

    见 docs/design/RUN_UNIFICATION.md
    """

    __tablename__ = "agent_runs"

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # 状态机：created/planning/executing/waiting/verifying/done/failed/cancelled
    status: Mapped[str] = mapped_column(String(20), default="created", index=True)
    mode: Mapped[str] = mapped_column(String(32), default="default")

    # Phase 2.1：统一 Run 维度
    # origin: chat|inbox|cron|cluster|subagent|headless
    origin: Mapped[str] = mapped_column(String(20), default="chat", index=True)
    identity_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("agent_identities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    parent_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # 2.3 权威 checkpoint；2.1 起可写镜像
    checkpoint: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    token_limit: Mapped[int] = mapped_column(Integer, default=0)
    token_used: Mapped[int] = mapped_column(Integer, default=0)

    # 输入/输出摘要（全文仍在 messages 表，这里只留可列表展示的摘要）
    input_summary: Mapped[str] = mapped_column(String(512), default="")
    final_summary: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 统计
    total_iterations: Mapped[int] = mapped_column(Integer, default=0)
    total_tool_calls: Mapped[int] = mapped_column(Integer, default=0)

    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ended_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # 扩展信息（cluster 角色数 / resume 来源 / 触发入口等）
    meta: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)


class RunStep(Base, UUIDMixin, TimestampMixin):
    """Run 内步骤流水：phase 迁移 / 工具调用 / 备注"""

    __tablename__ = "run_steps"

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer, default=0)

    # kind: phase / tool / iteration / note
    kind: Mapped[str] = mapped_column(String(20), default="note", index=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    # pending / running / completed / failed / skipped
    status: Mapped[str] = mapped_column(String(20), default="completed")

    # 工具调用参数摘要 / phase 迁移说明 / 结果摘要
    payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
