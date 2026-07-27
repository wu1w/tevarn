"""0.5 编制与档案：AI Workforce 持久层模型（PLAN_AI_WORKFORCE §3.a/3.b/3.c）。

四张表：
- AgentIdentity：持久身份（员工档案）。进程可以死，Agent 不死——
  身份不可销毁，只能 active/suspended/archived；权限档案随身份持久化。
- IdentityMemoryEntry：Identity Memory（四层记忆的第四层，行业空白）——
  人格/职责/经验/偏好/方法论。版本链 supersede，任何修改可追溯到审批人。
- KernelProcessRecord：kernel 进程档案（工牌持久化）。
  重启后 created/running → interrupted（诚实的中断记录，不伪造存活）。
- KernelCheckpoint：事件快照（PLAN 红线：checkpoint + 增量事件，
  禁止全量 replay）。快照是派生数据可丢弃，事件不可丢弃。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin


class AgentIdentity(Base, UUIDMixin, TimestampMixin):
    """持久 Agent 身份（员工档案）。

    生命周期：active → suspended ⇄ active → archived（终态，不可逆）。
    不可删除——审计链不可断（PLAN §3.a 红线）。
    """

    __tablename__ = "agent_identities"

    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # 由哪个岗位模板（SubAgent 配置）实例化；手工创建则为空
    sub_agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("sub_agents.id", ondelete="SET NULL"), nullable=True
    )

    # unique：提权/派活按 name 解析身份，重名会导致歧义
    name: Mapped[str] = mapped_column(String(64), index=True, unique=True)
    role: Mapped[str] = mapped_column(String(256), default="")  # 职责描述

    # 状态机：active / suspended / archived
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)

    # 权限档案：身份级能力集（None=兼容模式）。重启后不变、
    # 不可自行扩大——任何变更必须走审计事件（kernel 侧强制）。
    capabilities: Mapped[Any] = mapped_column(JSON, nullable=True)
    # 历史令牌签发记录（token id 链，签名验真在 kernel 侧）
    token_issuances: Mapped[Any] = mapped_column(JSON, default=list)
    default_token_budget: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # 信用评分（0-1）：绩效聚合。0.7 前只记录不消费（PLAN §3.e 克制原则）。
    credit_score: Mapped[float] = mapped_column(Float, default=1.0)

    meta: Mapped[Any] = mapped_column(JSON, default=dict)
    archived_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class IdentityMemoryEntry(Base, UUIDMixin, TimestampMixin):
    """Identity Memory 条目（四层记忆的第四层）。

    kind：persona 人格 / duty 职责 / experience 经验 /
          preference 偏好 / methodology 方法论
    修改不覆盖——新版本 supersede 旧版本（版本链），
    任何写入必须可追溯到审批人（PLAN §3.c 红线）。
    """

    __tablename__ = "identity_memory"

    identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_identities.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32), index=True)
    content: Mapped[str] = mapped_column(Text)
    # manual 手动 / distilled 蒸馏（走进化审批）/ system 系统写入
    source: Mapped[str] = mapped_column(String(32), default="manual")
    approved_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    # 版本链：被哪个新条目取代；NULL = 当前生效版本
    superseded_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("identity_memory.id", ondelete="SET NULL"), nullable=True
    )


class KernelProcessRecord(Base, UUIDMixin, TimestampMixin):
    """kernel 进程档案（工牌持久化）。

    与 agent_runs 的区别：agent_runs 是 loop 运行记录（Durable Execution），
    本表是 kernel 视角的进程（能力/预算/parent 链/终态）。
    重启恢复语义：created/running → interrupted（进程实际已死，
    档案诚实记录中断，不伪造存活）。
    """

    __tablename__ = "kernel_processes"

    process_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    identity_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("agent_identities.id", ondelete="SET NULL"), nullable=True, index=True
    )
    identity_key: Mapped[str] = mapped_column(String(64))  # agent_key（main/sub:xxx）
    session_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    parent_process_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    capabilities: Mapped[Any] = mapped_column(JSON, nullable=True)
    token_budget: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)

    # created/running/completed/failed/killed/interrupted
    state: Mapped[str] = mapped_column(String(16), default="created", index=True)
    started_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ended_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    meta: Mapped[Any] = mapped_column(JSON, default=dict)


class KernelCheckpoint(Base, UUIDMixin, TimestampMixin):
    """事件快照（PLAN §3.b 红线：checkpoint + 增量事件，禁止全量 replay）。

    快照是派生数据——可丢弃重建；事件（哈希链 JSONL）不可丢弃。
    恢复 = 最新快照 + 其后的增量事件。
    """

    __tablename__ = "kernel_checkpoints"

    seq: Mapped[int] = mapped_column(Integer, index=True)  # 快照序号
    event_count: Mapped[int] = mapped_column(Integer)  # 快照覆盖的事件总数
    tail_hash: Mapped[str] = mapped_column(String(64))  # 快照时链尾哈希（连续性锚点）
    state_snapshot: Mapped[Any] = mapped_column(JSON)  # 身份/进程状态序列化


class KernelEscalationRecord(Base, UUIDMixin, TimestampMixin):
    """提权申请外部化（多 worker 前提：pending 不能只活在单进程内存）。

    写路径：kernel request/approve/deny 经 persistence sink 同步 put_nowait。
    读路径：list_escalations 优先内存，shared 模式合并 DB pending。
    """

    __tablename__ = "kernel_escalations"

    escalation_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    process_id: Mapped[str] = mapped_column(String(32), index=True)
    capabilities: Mapped[Any] = mapped_column(JSON, default=list)
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    created_at_ts: Mapped[float] = mapped_column(Float, default=0.0)
    resolved_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    resolved_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class AgentInboxItem(Base, UUIDMixin, TimestampMixin):
    """Agent 收件箱工单（PLAN 阶段 0.6：cron/webhook/邮件/文件变更 → 工单）。

    Task 只是 Agent 生命周期中的一个事件——工单就是「一封信」，
    由 Dispatcher 唤醒对应身份来处理。

    状态机：pending → claimed → done/failed；
    溢出/拒收 → dropped（全部进审计链）。
    有界红线：全局 pending 超上限时丢弃最旧 pending（不无限堆积）。
    """

    __tablename__ = "agent_inbox_items"

    identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_identities.id", ondelete="CASCADE"), index=True
    )
    # cron / webhook / api / manual
    source: Mapped[str] = mapped_column(String(16), index=True)
    source_ref: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    instruction: Mapped[str] = mapped_column(Text)  # 任务描述（派活内容）
    payload: Mapped[Any] = mapped_column(JSON, default=dict)
    priority: Mapped[int] = mapped_column(Integer, default=0)

    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)

    result: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 执行它的 kernel 进程（审计链可回溯到 mediation/budget 全记录）
    process_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    claimed_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    finished_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class AgentEvolutionProposal(Base, UUIDMixin, TimestampMixin):
    """Agent 进化建议（PLAN 阶段 0.7：受控进化——述职报告式建议）。

    红线（PLAN §3.d）：
    - auto_apply=False 不可绕过——状态机没有「自动应用」路径，
      只能从 pending 经人工 approve/reject 流转；不存在配置项/
      环境变量/内部 API 形式的后门
    - payload.before 保存应用前状态 = 回滚点
    - 全生命周期（proposed/approved/applied/rejected/rolled_back）进哈希链
    """

    __tablename__ = "agent_evolution_proposals"

    identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_identities.id", ondelete="CASCADE"), index=True
    )
    # memory_distill 方法论沉淀 / tool_deprecate 工具淘汰 /
    # caps_adjust 能力调整 / planner_tune planner 自调
    kind: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(256))
    rationale: Mapped[str] = mapped_column(Text)  # 述职报告：数据支撑的理由
    payload: Mapped[Any] = mapped_column(JSON, default=dict)  # 含 before 回滚点

    # pending / approved / applied / rejected / rolled_back
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    resolved_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    resolved_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    applied_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    rolled_back_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
