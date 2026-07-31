"""AgentProcess —— Kernel 管理的执行实体。

.. deprecated:: P0-A / R3 去双轨
    **生产权威**：Rust ``takton_kernel::process``（经 host RPC）。
    本模块仅：单元测试直接构造、host 不可用时的 fallback。
    禁止在此扩展生产状态机；变更请改 crates/takton-kernel。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

ProcessState = Literal[
    "created",  # 已创建未运行
    "running",  # 执行中
    "suspended",  # 挂起（等待子进程 / 等待确认）
    "completed",  # 正常完成
    "failed",  # 异常终止
    "killed",  # 被 Kernel 中断（预算耗尽 / 权限违规 / 显式停止）
]

_TERMINAL_STATES = frozenset({"completed", "failed", "killed"})


@dataclass
class AgentProcess:
    identity: str  # agent_key（"main" / sub-agent key / workflow node）
    session_id: str | None = None
    parent_id: str | None = None
    # None = 未启用能力模型（兼容旧路径）；list = 显式能力集（ mediation 强制检查）
    capabilities: list[str] | None = None
    token_budget: int | None = None  # None = 不限
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    state: ProcessState = "created"
    tokens_used: int = 0
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    ended_at: float | None = None
    exit_reason: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    # W2：进程持有的能力令牌（可选）。挂载后 mediate 以令牌为准（含过期强制），
    # capabilities 列表退化为展示/继承语义。
    token: Any = None  # CapabilityToken，避免循环 import 用 Any
    # Phase 2（Alpha Review #1b）：suspend/resume 同步原语（lazy Event，
    # 初始 set = 非挂起；suspend 清空使 wait 阻塞，resume 置位放行）
    _resume_event: Any = field(default=None, repr=False, compare=False)

    def _event(self) -> asyncio.Event:
        if self._resume_event is None:
            self._resume_event = asyncio.Event()
            self._resume_event.set()
        return self._resume_event

    def suspend(self) -> None:
        """挂起：终态不可挂起；重复挂起幂等。"""
        if self.is_terminal:
            raise ValueError(f"进程 {self.id} 已终止（{self.state}），不可挂起")
        if self.state != "suspended":
            self.state = "suspended"
            self._event().clear()

    def resume(self) -> None:
        """恢复：仅 suspended → running；其他状态幂等无操作。"""
        if self.state == "suspended":
            self.state = "running"
            self._event().set()

    async def wait_if_suspended(
        self, *, poll: float = 0.5, should_stop: Any = None, refresh_state: Any = None
    ) -> bool:
        """挂起则阻塞等待恢复（轮询以便响应 stop/终态）。

        refresh_state: 可选回调，多 worker 时从 Redis 刷新 self.state
        （他 worker 的 resume 写 Redis，本 worker Event 不会 set）。
        返回 False = 等待被打断（stop 请求或进程已终态），调用方应中止。
        """
        while self.state == "suspended":
            if should_stop is not None and should_stop():
                return False
            if refresh_state is not None:
                try:
                    refresh_state(self)
                except Exception:
                    pass
                if self.state != "suspended":
                    break
            try:
                await asyncio.wait_for(self._event().wait(), timeout=poll)
            except asyncio.TimeoutError:
                pass
        return not self.is_terminal

    @property
    def is_terminal(self) -> bool:
        return self.state in _TERMINAL_STATES

    @property
    def budget_remaining(self) -> int | None:
        if self.token_budget is None:
            return None
        return max(0, self.token_budget - self.tokens_used)

    def has_capability(self, cap: str) -> bool:
        """None = 兼容模式全放行；显式能力集必须包含目标能力。

        编制层存抽象 cap（file_rw / command / web_search），
        mediate 传入的是工具名（file_read / glob / grep）——经 TOOL_TO_CREW_CAP 映射。
        """
        if self.capabilities is None:
            return True
        if cap in self.capabilities or "*" in self.capabilities:
            return True
        try:
            from backend.agent.grant_store import tool_matches_crew_caps

            return tool_matches_crew_caps(cap, self.capabilities)
        except Exception:
            return False

    def charge_tokens(self, amount: int) -> int | None:
        """扣减预算，返回剩余（None = 不限）。

        硬顶：若 amount 会把 tokens_used 顶穿 token_budget，则**不写入**并返回
        负哨兵语义由调用方（kernel.charge_tokens）转 BudgetExceededError。
        返回值仍为扣减后的 remaining；超支拒绝时抛 ValueError（kernel 捕获转换）。
        """
        if amount > 0:
            if self.token_budget is not None and self.tokens_used + amount > self.token_budget:
                raise ValueError(
                    f"charge {amount} would exceed budget "
                    f"({self.tokens_used}/{self.token_budget})"
                )
            self.tokens_used += amount
        return self.budget_remaining

    def to_dict(self) -> dict[str, Any]:
        token_payload = None
        if self.token is not None and hasattr(self.token, "to_dict"):
            try:
                token_payload = self.token.to_dict(sign=False)
            except TypeError:
                token_payload = self.token.to_dict()
        return {
            "id": self.id,
            "identity": self.identity,
            "session_id": self.session_id,
            "parent_id": self.parent_id,
            "capabilities": self.capabilities,
            "token_budget": self.token_budget,
            "tokens_used": self.tokens_used,
            "budget_remaining": self.budget_remaining,
            "state": self.state,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "exit_reason": self.exit_reason,
            "meta": self.meta,
            "token": token_payload,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentProcess":
        """从 Redis/DB 字典水合（无 resume Event；跨 worker 挂起靠 state 轮询）。"""
        proc = cls(
            identity=str(data.get("identity") or "main"),
            session_id=data.get("session_id"),
            parent_id=data.get("parent_id"),
            capabilities=data.get("capabilities"),
            token_budget=data.get("token_budget"),
            id=str(data.get("id") or uuid.uuid4().hex[:16]),
            state=data.get("state") or "created",  # type: ignore[arg-type]
            tokens_used=int(data.get("tokens_used") or 0),
            created_at=float(data.get("created_at") or time.time()),
            started_at=data.get("started_at"),
            ended_at=data.get("ended_at"),
            exit_reason=data.get("exit_reason"),
            meta=dict(data.get("meta") or {}),
        )
        tok = data.get("token")
        if isinstance(tok, dict) and tok.get("capabilities") is not None:
            try:
                from backend.kernel.capability import CapabilityToken

                proc.token = CapabilityToken.from_dict(tok, verify=False)
            except Exception:
                proc.token = None
        return proc
