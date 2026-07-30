"""ClusterRun 模型（Phase 3：cluster 结果持久化）

一次集群执行（/cluster/execute | execute-plan | quick）的持久化记录。
此前 _active_clusters 纯内存，重启即丢；落库后 status/list 可跨重启查询，
运行中的记录在启动清扫时标记为 interrupted（诚实，不假装还在跑）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin


class ClusterRun(Base, UUIDMixin, TimestampMixin):
    """一次集群执行的持久化记录"""

    __tablename__ = "cluster_runs"

    # 路由层句柄（/cluster/status/{task_id}）
    task_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    plan_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    name: Mapped[str] = mapped_column(String(255), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    # running/completed/failed/cancelled/interrupted
    status: Mapped[str] = mapped_column(String(20), default="running", index=True)
    aggregation_strategy: Mapped[str] = mapped_column(String(32), default="synthesize")

    # 完整执行结果（SubTask.to_dict() 列表，含 deliverable/review 元数据）
    sub_tasks: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSON, nullable=True)
    aggregated_result: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    # 复核汇总 {"reviewed": n, "rejected": m}
    review: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    sub_task_count: Mapped[int] = mapped_column(Integer, default=0)

    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ended_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def to_status_dict(self) -> dict[str, Any]:
        """对齐 /cluster/status 响应形状"""
        return {
            "task_id": self.task_id,
            "status": self.status,
            "sub_tasks": self.sub_tasks or [],
            "aggregated_result": self.aggregated_result,
            "error": self.error,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.ended_at.isoformat() if self.ended_at else None,
            "review": self.review,
        }
