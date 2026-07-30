"""Agent Run 状态机（Phase 0.5.2 Durable Execution）

状态流：CREATED → PLANNING → EXECUTING → WAITING → VERIFYING → DONE / FAILED
- WAITING：等待人工批准 / 外部输入，可回到 EXECUTING
- CANCELLED：任意非终态可被取消（stop 按钮 / 超时）
- 非法迁移抛 IllegalTransitionError，由调用方决定降级策略
"""
from __future__ import annotations

from enum import Enum as PyEnum


class RunStatus(str, PyEnum):
    CREATED = "created"
    PLANNING = "planning"
    EXECUTING = "executing"
    WAITING = "waiting"
    VERIFYING = "verifying"
    # Phase 2.3：进程被 kill / 启动扫到的非终态 → interrupted（可续跑）
    INTERRUPTED = "interrupted"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATES: frozenset[RunStatus] = frozenset(
    {RunStatus.DONE, RunStatus.FAILED, RunStatus.CANCELLED}
)

# 合法迁移表：key → 可达状态集合（终态 DONE/FAILED/CANCELLED 从任意非终态可达）
_NON_TERMINAL_EXITS = frozenset(
    {
        RunStatus.INTERRUPTED,
        RunStatus.DONE,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    }
)

TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.CREATED: frozenset(
        {
            RunStatus.PLANNING,
            RunStatus.EXECUTING,
            RunStatus.DONE,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.INTERRUPTED,
        }
    ),
    RunStatus.PLANNING: frozenset(
        {
            RunStatus.EXECUTING,
            RunStatus.WAITING,
            RunStatus.VERIFYING,
            RunStatus.DONE,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.INTERRUPTED,
        }
    ),
    RunStatus.EXECUTING: frozenset(
        {
            RunStatus.WAITING,
            RunStatus.VERIFYING,
            RunStatus.DONE,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.INTERRUPTED,
        }
    ),
    RunStatus.WAITING: frozenset(
        {
            RunStatus.EXECUTING,
            RunStatus.DONE,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.INTERRUPTED,
        }
    ),
    RunStatus.VERIFYING: frozenset(
        {
            RunStatus.EXECUTING,
            RunStatus.DONE,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.INTERRUPTED,
        }
    ),
    # interrupted → 续跑回 executing，或放弃
    RunStatus.INTERRUPTED: frozenset(
        {
            RunStatus.EXECUTING,
            RunStatus.PLANNING,
            RunStatus.DONE,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
    ),
    RunStatus.DONE: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}


class IllegalTransitionError(ValueError):
    """非法状态迁移"""


def can_transition(src: RunStatus | str, dst: RunStatus | str) -> bool:
    src_s, dst_s = RunStatus(src), RunStatus(dst)
    if src_s == dst_s:
        return True  # 同态重写（如重复上报 executing）视为合法 no-op
    return dst_s in TRANSITIONS.get(src_s, frozenset())


def validate_transition(src: RunStatus | str, dst: RunStatus | str) -> RunStatus:
    """校验迁移，非法则抛 IllegalTransitionError；合法返回目标状态枚举"""
    dst_s = RunStatus(dst)
    if not can_transition(src, dst_s):
        raise IllegalTransitionError(f"illegal run transition: {src} -> {dst}")
    return dst_s
