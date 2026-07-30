"""AgentKernel —— 控制平面骨架（阶段 1 / W1）。

职责边界（对应计划的五大职能）：
  1. 进程管理：create_process / end_process / 生命周期状态机
  2. 能力模型：AgentProcess.capabilities + CapabilityToken（W2 全量）
  3. 执行中介：mediate() —— W1 记录审计事件 + 显式能力集强制检查，
     W3 所有 tool/skill/MCP 调用统一收口到这里
  4. 预算治理：charge_tokens / 超限判定（强制中断在 W2-阶段2 完善）
  5. 可观测性：每次中介/生命周期变迁都产生不可变审计事件（哈希链，阶段 3）

渐进原则：capabilities=None 的进程走兼容模式（放行 + 记录），
现有 loop 行为不变；显式能力集才强制检查。

并发假设（审计项 #6，已文档化）：
  本类所有 public async 方法内部**不含任何 await**——纯同步逻辑，
  在 asyncio 单线程语义下不存在竞态窗口（事件循环不会在中途出让）。
  ⚠ 维护红线：在 create_process / mediate / charge_tokens / _emit
  内部引入任何 await 之前，必须先为 _processes / _events / scheduler
  加 asyncio.Lock（参考 loop.py 契约白名单的竞态教训）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from backend.kernel.capability import CapabilityEscalationError, CapabilityToken
from backend.kernel.process import AgentProcess

logger = logging.getLogger(__name__)

MediationAction = Literal["tool_call", "skill_exec", "mcp_call", "command_exec", "subagent_spawn"]

_GENESIS_HASH = "0" * 64


def _event_hash(prev_hash: str, kind: str, process_id: str, detail: dict, ts: float, eid: str) -> str:
    """事件内容哈希（SHA-256）。链式：每条事件哈希包含前一条的哈希，
    篡改任何历史事件都会导致其后所有哈希校验失败。"""
    payload = json.dumps(
        {
            "prev": prev_hash,
            "kind": kind,
            "process_id": process_id,
            "detail": detail,
            "ts": ts,
            "id": eid,
        },
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class KernelEvent:
    """不可变审计事件。哈希链持久化（阶段 3）：prev_hash 链接前一条事件。"""

    kind: str  # process_created / process_ended / mediation / budget_exceeded
    process_id: str
    detail: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    ts: float = field(default_factory=time.time)
    prev_hash: str = _GENESIS_HASH
    hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "process_id": self.process_id,
            "detail": self.detail,
            "ts": self.ts,
            "prev_hash": self.prev_hash,
            "hash": self.hash,
        }


@dataclass(frozen=True)
class MediationDecision:
    allowed: bool
    reason: str = ""
    capability_checked: bool = False  # True = 显式能力集裁决；False = 兼容模式放行


class KernelPermissionError(PermissionError):
    """mediate() 拒绝执行时抛出。"""

    def __init__(self, message: str, decision: MediationDecision) -> None:
        super().__init__(message)
        self.decision = decision


class BudgetExceededError(RuntimeError):
    """进程预算耗尽。"""


@dataclass(frozen=True)
class EscalationRequest:
    """提权申请（0.4.1 地基：「劳动合同补充条款」的签署流程）。

    与能力单调递减红线的关系：narrowing 约束的是**父子派生**
    （子不能超父，数据结构级）；escalation 是**控制面授权**——
    kernel 作为 trusted 根，代表用户把能力并入进程能力集。
    用户显式批准是唯一合法的能力扩大通道，全程哈希链留痕。
    """

    id: str
    process_id: str
    capabilities: tuple[str, ...]
    reason: str = ""
    status: str = "pending"  # pending / approved / denied
    created_at: float = 0.0
    resolved_at: float | None = None
    resolved_by: str | None = None
    # 批准后写入：process = 并入 live 进程；identity = 并入编制档案
    target: str | None = None  # "process" | "identity" | None
    identity_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "process_id": self.process_id,
            "capabilities": list(self.capabilities),
            "reason": self.reason,
            "status": self.status,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "resolved_by": self.resolved_by,
        }
        if self.target is not None:
            d["target"] = self.target
        if self.identity_id is not None:
            d["identity_id"] = self.identity_id
        return d


_EVENT_BUFFER_MAX = 5000


class AgentKernel:
    def __init__(
        self,
        audit_store: Any | None = None,
        persistence_sink: Any | None = None,
        shared_store: Any | None = None,
    ) -> None:
        self._processes: dict[str, AgentProcess] = {}
        self._events: list[KernelEvent] = []
        self._escalations: dict[str, EscalationRequest] = {}
        # 0.5：持久化 sink（kernel/persistence.py）。同步 put_nowait 零 await，
        # 符合单线程红线；异步消费者落盘（进程档案 + checkpoint 计数）。
        self._persistence_sink = persistence_sink
        # 多 worker：Redis 共享进程/提权（shared_store.py）；None = 单进程内存
        self._shared = shared_store
        # 0.5：身份注册表（kernel/identity.py，外部装配，None = 身份层关闭）
        self.identity_registry: Any | None = None
        # 阶段 3：审计落盘。挂载后每条事件追加 JSONL；
        # 内存缓冲为空时从文件链尾续 prev_hash（跨重启链连续）。
        self._audit_store = audit_store
        self._disk_tail_hash: str | None = (
            audit_store.load_tail_hash() if audit_store is not None else None
        )
        # 阶段 2：多 Agent 调度器（优先级 + aging 公平性）
        from backend.kernel.scheduler import AgentScheduler

        self._scheduler = AgentScheduler()

    @property
    def scheduler(self) -> Any:
        return self._scheduler

    # ── 持久化挂钩（0.5：同步 sink，零 await）────────────────

    def _persist_process(self, proc: "AgentProcess") -> None:
        if self._persistence_sink is not None:
            try:
                self._persistence_sink({"op": "process_upsert", "data": proc.to_dict()})
            except Exception as e:
                logger.warning("kernel 持久化 sink 失败（不阻断）: %s", e)
        self._share_process(proc)

    def _share_process(self, proc: "AgentProcess") -> None:
        """同步写 Redis（多 worker 可见）。失败不阻断。"""
        if self._shared is None:
            return
        try:
            self._shared.put_process(proc.to_dict())
        except Exception as e:
            logger.warning("kernel Redis put_process 失败（不阻断）: %s", e)

    def _resolve_process(self, process_id: str) -> AgentProcess | None:
        """本地缓存 + Redis 权威合并（多 worker mediate 入口）。

        - tokens_used：永远取 max(local, redis)（计数权威在 Redis HINCRBY）
        - 其它字段：仅当 redis.updated_at >= local._sync_at 时覆盖，
          避免刚写本地、尚未 put 完时被旧 Redis 快照盖掉
        """
        if self._shared is not None:
            try:
                data = self._shared.get_process(process_id)
            except Exception as e:
                logger.warning("kernel Redis get_process 失败: %s", e)
                data = None
            if data is not None:
                local = self._processes.get(process_id)
                remote_ts = float(data.get("updated_at") or 0)
                if local is None:
                    local = AgentProcess.from_dict(data)
                    local.meta = dict(local.meta or {})
                    local.meta["_sync_at"] = remote_ts or time.time()
                    self._processes[process_id] = local
                else:
                    local_ts = float((local.meta or {}).get("_sync_at") or 0)
                    # 计数永远向 Redis 看齐（单调）
                    remote_used = int(data.get("tokens_used") or 0)
                    if remote_used > local.tokens_used:
                        local.tokens_used = remote_used
                    if remote_ts >= local_ts:
                        local.capabilities = data.get("capabilities")
                        local.token_budget = data.get("token_budget")
                        st = data.get("state")
                        if st and st != local.state:
                            local.state = st  # type: ignore[assignment]
                        local.exit_reason = data.get("exit_reason")
                        tok = data.get("token")
                        if isinstance(tok, dict) and tok.get("capabilities") is not None:
                            try:
                                local.token = CapabilityToken.from_dict(tok, verify=False)
                            except Exception:
                                pass
                        local.meta = dict(local.meta or {})
                        local.meta["_sync_at"] = remote_ts
                return local
        return self._processes.get(process_id)

    # ── 进程管理 ──────────────────────────────────────────────

    async def create_process(
        self,
        identity: str,
        *,
        session_id: str | None = None,
        parent_id: str | None = None,
        capabilities: list[str] | None = None,
        token_budget: int | None = None,
        meta: dict[str, Any] | None = None,
    ) -> AgentProcess:
        """创建进程。指定 parent_id 时能力自动收窄为父进程子集（不可提权），
        预算不得超过父进程剩余预算。"""
        parent = self._resolve_process(parent_id) if parent_id else None
        effective_caps = capabilities
        effective_budget = token_budget

        if parent is not None:
            if parent.is_terminal:
                raise ValueError(f"父进程 {parent_id} 已终止（{parent.state}），无法派生子进程")
            if capabilities is not None and parent.capabilities is not None:
                extra = set(capabilities) - set(parent.capabilities)
                if "*" in parent.capabilities:
                    extra = set()
                if extra:
                    raise CapabilityEscalationError(
                        f"子进程能力 {sorted(extra)} 超出父进程能力集"
                    )
            elif capabilities is not None and parent.capabilities is None:
                # 父为兼容模式（全量）：子进程的显式能力集天然是子集，合法
                pass
            elif capabilities is None:
                # 未指定则继承父能力集（保持单调递减语义）
                effective_caps = list(parent.capabilities) if parent.capabilities else parent.capabilities

            if token_budget is not None and parent.budget_remaining is not None:
                if token_budget > parent.budget_remaining:
                    raise BudgetExceededError(
                        f"子进程预算 {token_budget} 超过父进程剩余预算 {parent.budget_remaining}"
                    )
                # 预留：从父进程扣减等额额度，防止多子进程双花同一份 remaining
                try:
                    self.charge_tokens(parent.id, int(token_budget))
                except BudgetExceededError as e:
                    raise BudgetExceededError(
                        f"父进程预算预留失败（子要 {token_budget}）：{e}"
                    ) from e
                # charge 后刷新 parent 引用
                parent = self._resolve_process(parent_id) or parent

        proc = AgentProcess(
            identity=identity,
            session_id=session_id,
            parent_id=parent_id,
            capabilities=effective_caps,
            token_budget=effective_budget,
            meta=dict(meta or {}),
        )
        self._processes[proc.id] = proc
        proc.meta = dict(proc.meta or {})
        proc.meta["_sync_at"] = time.time()
        self._emit("process_created", proc.id, {
            "identity": identity,
            "session_id": session_id,
            "parent_id": parent_id,
            "capabilities": effective_caps,
            "token_budget": effective_budget,
        })
        self._persist_process(proc)
        if self._shared is not None:
            try:
                self._shared.record_daily_run()
            except Exception:
                pass
        return proc

    async def end_process(
        self,
        process_id: str,
        *,
        state: Literal["completed", "failed", "killed"] = "completed",
        reason: str | None = None,
    ) -> AgentProcess | None:
        proc = self._resolve_process(process_id)
        if proc is None:
            logger.warning("end_process: 未知进程 %s", process_id)
            return None
        if proc.is_terminal:
            return proc
        proc.state = state
        proc.ended_at = time.time()
        proc.exit_reason = reason
        self._emit("process_ended", proc.id, {
            "state": state,
            "reason": reason,
            "tokens_used": proc.tokens_used,
            "duration_ms": int((proc.ended_at - (proc.started_at or proc.created_at)) * 1000),
        })
        self._persist_process(proc)
        return proc

    async def mark_running(self, process_id: str) -> None:
        proc = self._resolve_process(process_id)
        if proc is not None and proc.state == "created":
            proc.state = "running"
            proc.started_at = time.time()
            self._persist_process(proc)

    # ── 挂起 / 恢复（Phase 2：Alpha Review #1b）──────────────────

    async def suspend_process(self, process_id: str, *, reason: str = "") -> AgentProcess:
        """挂起运行中进程：loop 在下一轮 iteration gate 处阻塞等待恢复。
        终态进程挂起抛错；重复挂起幂等。多 worker 时 state 写 Redis。"""
        proc = self._resolve_process(process_id)
        if proc is None:
            raise ValueError(f"未知进程 {process_id}")
        proc.suspend()  # 终态校验在 process 层
        if reason:
            proc.meta["suspend_reason"] = reason
        self._persist_process(proc)
        self._emit("process_suspended", process_id, {"reason": reason})
        return proc

    async def resume_process(self, process_id: str) -> AgentProcess:
        """恢复挂起进程：仅 suspended → running；其他状态幂等。"""
        proc = self._resolve_process(process_id)
        if proc is None:
            raise ValueError(f"未知进程 {process_id}")
        was = proc.state
        proc.resume()
        if was == "suspended":
            proc.meta.pop("suspend_reason", None)
            self._persist_process(proc)
            self._emit("process_resumed", process_id, {})
            if self._shared is not None:
                try:
                    self._shared.publish_resume(process_id)
                except Exception:
                    pass
        return proc

    def get_process(self, process_id: str) -> AgentProcess | None:
        return self._resolve_process(process_id)

    def list_processes(self, *, include_terminal: bool = False) -> list[AgentProcess]:
        # 多 worker：合并 Redis 索引中的进程
        if self._shared is not None:
            try:
                for pid in self._shared.list_process_ids():
                    self._resolve_process(pid)
            except Exception as e:
                logger.warning("list_processes redis merge: %s", e)
        if include_terminal:
            return list(self._processes.values())
        return [p for p in self._processes.values() if not p.is_terminal]

    def live_processes_for_identity(self, identity: str) -> list[AgentProcess]:
        """非终态进程，按 identity 键精确匹配（如 wf:{uuid}）。"""
        key = str(identity or "").strip()
        if not key:
            return []
        return [p for p in self.list_processes(include_terminal=False) if p.identity == key]

    async def retire_live_identity_processes(
        self,
        identity: str,
        *,
        reason: str = "superseded by new job",
        except_process_id: str | None = None,
    ) -> list[str]:
        """编制防双跑：结束同 identity 下所有非终态进程。

        返回被 kill 的 process_id 列表。
        """
        killed: list[str] = []
        for p in self.live_processes_for_identity(identity):
            if except_process_id and p.id == except_process_id:
                continue
            try:
                await self.end_process(p.id, state="killed", reason=reason)
                killed.append(p.id)
                logger.warning(
                    "retired stale process %s identity=%s reason=%s",
                    p.id[:8],
                    identity[:24],
                    reason[:80],
                )
            except Exception as e:
                logger.warning("retire process %s failed: %s", p.id[:8], e)
        return killed

    def top_up_budget(
        self,
        process_id: str,
        amount: int,
        *,
        by: str = "ceo",
        reason: str = "",
    ) -> dict[str, Any]:
        """运行中追加 token 预算（CEO 动态加预算）。

        - token_budget is None（不限）→ 幂等返回 unlimited
        - 终态进程 → 拒绝
        - amount 必须为正
        下一刀 charge_tokens 立即使用新上限。
        """
        amount = int(amount)
        if amount <= 0:
            raise ValueError("top_up amount 必须为正整数")
        proc = self._resolve_process(process_id)
        if proc is None:
            raise ValueError(f"未知进程 {process_id}")
        if proc.is_terminal:
            raise ValueError(f"进程已终态（{proc.state}），不可追加预算")
        if proc.token_budget is None:
            return {
                "ok": True,
                "unlimited": True,
                "process_id": proc.id,
                "token_budget": None,
                "tokens_used": proc.tokens_used,
                "budget_remaining": None,
            }
        old = int(proc.token_budget)
        new_b = old + amount
        proc.token_budget = new_b
        proc.meta = dict(proc.meta or {})
        proc.meta["_sync_at"] = time.time()
        tops = list(proc.meta.get("budget_top_ups") or [])
        tops.append(
            {
                "amount": amount,
                "by": by,
                "reason": (reason or "")[:200],
                "at": time.time(),
                "from": old,
                "to": new_b,
            }
        )
        proc.meta["budget_top_ups"] = tops[-20:]
        if self._shared is not None:
            try:
                self._shared.set_process_fields(proc.id, token_budget=new_b)
            except Exception as e:
                logger.debug("redis top_up budget: %s", e)
        self._persist_process(proc)
        self._emit(
            "budget_top_up",
            proc.id,
            {
                "from": old,
                "to": new_b,
                "amount": amount,
                "by": by,
                "reason": (reason or "")[:200],
                "tokens_used": proc.tokens_used,
            },
        )
        return {
            "ok": True,
            "unlimited": False,
            "process_id": proc.id,
            "token_budget": new_b,
            "tokens_used": proc.tokens_used,
            "budget_remaining": max(0, new_b - proc.tokens_used),
            "added": amount,
            "by": by,
        }

    # ── 执行中介 ──────────────────────────────────────────────

    def _emit_policy_decision(
        self,
        process_id: str,
        *,
        action: Any,
        target: str,
        outcome: str,
        reason: str,
        source: str = "kernel",
        identity: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """0.5.2 权限一张网：统一 policy.decision 事件（who/what/allow|deny|escalate）。

        与历史 mediation 事件并存——Security Console / 审计页优先消费本事件。
        outcome: allow | deny | escalate
        """
        detail: dict[str, Any] = {
            "who": identity or process_id,
            "what": f"{action}:{target}",
            "action": str(action),
            "target": target,
            "outcome": outcome,
            "allowed": outcome == "allow",
            "reason": reason,
            "source": source,
        }
        if extra:
            detail.update(extra)
        self._emit("policy.decision", process_id, detail)

    async def mediate(
        self,
        process_id: str,
        action: MediationAction,
        target: str,
        args: dict[str, Any] | None = None,
    ) -> MediationDecision:
        """所有执行动作的统一入口（W3 全路径收口）。

        Phase 3.2：能力层经 permission_court.decide_capability；
        policy.decision 携带 tool/args_digest/verdict/matched_rule/layer。
        （路径/profile/DSL 等工具规则在 tool_hooks → court.decide_tool。）
        """
        from backend.kernel.permission_court import decide_capability

        proc = self._resolve_process(process_id)
        court = decide_capability(
            process_id=process_id,
            action=str(action),
            target=target,
            proc=proc,
            args=args,
        )
        ident = None
        if proc is not None:
            ident = getattr(proc, "identity", None) or getattr(proc, "identity_key", None)

        audit = court.to_audit()
        outcome = "allow" if court.verdict == "allow" else "deny"
        pid = proc.id if proc is not None else process_id

        detail = {
            "action": action,
            "target": target,
            "allowed": court.verdict == "allow",
            "reason": court.reason,
            "capability_checked": court.capability_checked,
            "args_keys": sorted((args or {}).keys()),
            **audit,
        }
        self._emit("mediation", pid, detail)
        self._emit_policy_decision(
            pid,
            action=action,
            target=target,
            outcome=outcome,
            reason=court.reason or court.matched_rule,
            source="permission_court",
            identity=ident,
            extra=audit,
        )

        if court.verdict != "allow":
            decision = MediationDecision(
                False, court.reason, capability_checked=court.capability_checked
            )
            raise KernelPermissionError(decision.reason, decision)

        return MediationDecision(True, capability_checked=court.capability_checked)

    # ── 预算治理 ──────────────────────────────────────────────

    def charge_tokens(self, process_id: str, amount: int) -> int | None:
        """扣减进程预算，返回剩余。超限抛 BudgetExceededError（调用方决定中断策略）。

        多 worker：Redis HINCRBY 原子扣减，再同步本地缓存。
        硬顶：单次 charge 不得把 tokens_used 顶穿 budget（拒绝写入）。
        """
        proc = self._resolve_process(process_id)
        if proc is None:
            return None
        if amount > 0 and proc.token_budget is not None:
            if proc.tokens_used + amount > proc.token_budget:
                self._emit("budget_exceeded", proc.id, {
                    "token_budget": proc.token_budget,
                    "tokens_used": proc.tokens_used,
                    "rejected_charge": amount,
                })
                raise BudgetExceededError(
                    f"进程 {process_id} 预算不足（已用 {proc.tokens_used}/{proc.token_budget}，拒绝 +{amount}）"
                )
        if self._shared is not None and amount > 0:
            try:
                used, remaining = self._shared.charge_tokens(process_id, amount)
                if used is not None:
                    proc.tokens_used = used
                # 成功扣到 0 = 预算用尽但本刀合法，不抛；下一刀由硬顶拒绝
                if remaining is not None and remaining == 0:
                    self._emit("budget_exhausted", proc.id, {
                        "token_budget": proc.token_budget,
                        "tokens_used": proc.tokens_used,
                    })
                # 写入 DB 档案，否则 UI/org 聚合 tokens_used 永远为 0
                self._persist_process(proc)
                self._maybe_auto_tighten(proc)
                return remaining
            except BudgetExceededError:
                raise
            except RuntimeError as e:
                if "budget exceeded" in str(e).lower():
                    self._emit("budget_exceeded", proc.id, {
                        "token_budget": proc.token_budget,
                        "tokens_used": proc.tokens_used,
                        "rejected_charge": amount,
                    })
                    raise BudgetExceededError(str(e)) from e
                logger.warning("redis charge_tokens RuntimeError，回退本地: %s", e)
            except Exception as e:
                logger.warning("redis charge_tokens 失败，回退本地: %s", e)
        try:
            remaining = proc.charge_tokens(amount)
        except ValueError as e:
            self._emit("budget_exceeded", proc.id, {
                "token_budget": proc.token_budget,
                "tokens_used": proc.tokens_used,
                "rejected_charge": amount,
            })
            raise BudgetExceededError(str(e)) from e
        # 回退路径：本地扣完后 put + DB 档案（UI 成本/预算条依赖）
        self._persist_process(proc)
        if remaining is not None and remaining == 0:
            self._emit("budget_exhausted", proc.id, {
                "token_budget": proc.token_budget,
                "tokens_used": proc.tokens_used,
            })
        self._maybe_auto_tighten(proc)
        return remaining

    def _maybe_auto_tighten(self, proc: AgentProcess) -> None:
        """auto_tighten_2x：单进程用量 > 2× 他进程日均/run 时收紧 token_budget。

        日均排除本进程已用量，避免刚 charge 抬高均值导致阈值永不触发。
        """
        try:
            from backend.kernel.approval_rules import rule_enabled_sync

            if not rule_enabled_sync("auto_tighten_2x", True):
                return
            if proc.token_budget is None or proc.tokens_used <= 0:
                return
            avg = 0.0
            if self._shared is not None:
                avg = float(
                    self._shared.daily_avg_per_run(exclude_tokens=proc.tokens_used) or 0
                )
            if avg <= 0:
                return
            if proc.tokens_used <= 2.0 * avg:
                return
            # 收紧到「当前已用 + 5% 余量」，且不得放宽
            tightened = max(proc.tokens_used + 500, int(proc.tokens_used * 1.05))
            if tightened >= proc.token_budget:
                return
            old = proc.token_budget
            proc.token_budget = tightened
            if self._shared is not None:
                self._shared.set_process_fields(proc.id, token_budget=tightened)
            # 同步本地 meta 时间戳，避免下次 resolve 被旧 Redis 盖回宽预算
            proc.meta = dict(proc.meta or {})
            proc.meta["_sync_at"] = time.time()
            self._emit("budget_tightened", proc.id, {
                "from": old,
                "to": tightened,
                "tokens_used": proc.tokens_used,
                "daily_avg_per_run": avg,
                "rule": "auto_tighten_2x",
            })
        except Exception as e:
            logger.debug("auto_tighten skip: %s", e)

    # ── 能力令牌 ──────────────────────────────────────────────

    def issue_token(
        self,
        process_id: str,
        capabilities: list[str] | set[str] | frozenset[str] | None = None,
        *,
        expires_at: float | None = None,
    ) -> CapabilityToken:
        """为进程签发能力令牌。默认取进程当前能力集；显式子集等价于 narrow。"""
        proc = self._resolve_process(process_id)
        if proc is None:
            raise ValueError(f"未知进程 {process_id}")
        caps = capabilities if capabilities is not None else (proc.capabilities or ["*"])
        token = CapabilityToken(
            capabilities=frozenset(caps),
            process_id=process_id,
            expires_at=expires_at,
        )
        if proc.capabilities is not None and "*" not in proc.capabilities:
            extra = set(token.capabilities) - set(proc.capabilities)
            if extra:
                raise CapabilityEscalationError(
                    f"令牌能力 {sorted(extra)} 超出进程能力集"
                )
        proc.token = token  # 挂载后 mediate 以令牌为准（含过期强制）
        self._share_process(proc)
        return token

    # ── 提权交互（0.4.1：用户授权是唯一合法的能力扩大通道）──────────────

    async def request_escalation(
        self,
        process_id: str,
        capabilities: list[str] | set[str],
        *,
        reason: str = "",
    ) -> EscalationRequest:
        """进程申请扩大能力集。pending 状态等待用户批准/拒绝。

        兼容模式进程（capabilities=None）本就全放行，申请无意义——拒绝。
        多 worker：Redis SETNX claim 防并发双 pending（同 process+caps 指纹）。
        """
        proc = self._resolve_process(process_id)
        if proc is None:
            raise ValueError(f"未知进程 {process_id}")
        if proc.is_terminal:
            raise ValueError(f"进程已终止（{proc.state}），无法提权")
        if proc.capabilities is None:
            raise ValueError("兼容模式进程（无显式能力集）无需提权")
        caps = tuple(sorted(set(capabilities) - set(proc.capabilities)))
        if not caps:
            raise ValueError("申请的能力均已在进程能力集内")
        # 去重：同进程已有 pending 申请覆盖这些能力时直接复用——
        # 模型被拦截后可能重试，不能每次拦截都刷一条新申请
        pending_caps = set(caps)
        for existing in self._escalations.values():
            if (
                existing.process_id == process_id
                and existing.status == "pending"
                and pending_caps <= set(existing.capabilities)
            ):
                return existing
        # 跨 worker：查 Redis pending（他 worker 已申请）
        if self._shared is not None:
            try:
                remote = self._shared.find_covering_pending(process_id, caps)
                if remote:
                    self.hydrate_escalation(remote)
                    hit = self._escalations.get(str(remote.get("id") or ""))
                    if hit is not None:
                        return hit
            except Exception as e:
                logger.debug("redis escalation dedup skip: %s", e)
        candidate_id = uuid.uuid4().hex[:16]
        # SETNX 原子占坑：并发 request 只允许一个 owner 创建 pending
        if self._shared is not None:
            try:
                owner = self._shared.try_claim_escalation(
                    process_id, caps, candidate_id
                )
                if owner != candidate_id:
                    # 他 worker 已占坑：水合已有申请并复用
                    data = self._shared.get_escalation(owner)
                    if data:
                        self.hydrate_escalation(data)
                        hit = self._escalations.get(owner)
                        if hit is not None:
                            return hit
                    remote = self._shared.find_covering_pending(process_id, caps)
                    if remote:
                        self.hydrate_escalation(remote)
                        hit = self._escalations.get(str(remote.get("id") or ""))
                        if hit is not None:
                            return hit
                    # claim 已有但 record 尚未 put：返回 stub 指向 owner id
                    stub = EscalationRequest(
                        id=owner,
                        process_id=process_id,
                        capabilities=caps,
                        reason=reason,
                        created_at=time.time(),
                    )
                    self._escalations[stub.id] = stub
                    return stub
            except Exception as e:
                logger.debug("redis escalation claim skip: %s", e)
        req = EscalationRequest(
            id=candidate_id,
            process_id=process_id,
            capabilities=caps,
            reason=reason,
            created_at=time.time(),
        )
        self._escalations[req.id] = req
        self._persist_escalation(req)
        self._emit("escalation_requested", process_id, {
            "escalation_id": req.id,
            "capabilities": list(caps),
            "reason": reason,
        })
        ident = getattr(proc, "identity", None) or getattr(proc, "identity_key", None)
        self._emit_policy_decision(
            process_id,
            action="escalation",
            target=",".join(caps),
            outcome="escalate",
            reason=reason or "capability_escalation",
            source="kernel",
            identity=ident,
            extra={"escalation_id": req.id, "capabilities": list(caps)},
        )
        # 审批规则：低风险 + auto_low_risk 开启 → 自动批准（不打扰老板）
        try:
            from backend.kernel.approval_rules import should_auto_approve_escalation

            if await should_auto_approve_escalation(list(caps)):
                return await self.approve_escalation(req.id, by="auto:approval_rules")
        except Exception as e:
            logger.debug("auto-approve check skipped: %s", e)
        # 仍 pending：通知主人去审批中心（扩权，非工具洪水）
        try:
            await self._notify_escalation_pending(req, identity=ident)
        except Exception as e:
            logger.debug("escalation notify skip: %s", e)
        return req

    async def _notify_escalation_pending(self, req: EscalationRequest, *, identity: str | None) -> None:
        """待批扩权 → 系统通知（单用户取 admin）。"""
        from backend.repositories.notification_repo import AsyncNotificationRepository
        from backend.repositories.user_repo import AsyncUserRepository

        u = await AsyncUserRepository().get_by_email("admin@takton.dev")
        if u is None:
            return
        caps = ", ".join(req.capabilities[:8])
        await AsyncNotificationRepository().create(
            {
                "user_id": u.id,
                "type": "system",
                "title": f"待批员工扩权 · {caps}"[:256],
                "content": (req.reason or f"进程 {req.process_id} 申请能力 {caps}")[:4000],
                "is_read": False,
                "data": {
                    "escalation_id": req.id,
                    "process_id": req.process_id,
                    "capabilities": list(req.capabilities),
                    "identity": identity or "",
                    "source": "kernel_escalation",
                },
                "source_id": str(req.id)[:64],
            }
        )

    async def approve_escalation(self, request_id: str, *, by: str = "user") -> EscalationRequest:
        """批准提权：优先并入 live 进程能力集；进程已死则并入 identity 档案。

        跨 worker：调用前需 ensure_escalation_loaded。
        重启后进程 interrupted 不复活——能力落到编制档案，下次派活生效。
        """
        req = self._escalations.get(request_id)
        if req is None:
            raise ValueError(f"未知提权申请 {request_id}")
        if req.status != "pending":
            raise ValueError(f"申请已处理（{req.status}）")
        proc = self._resolve_process(req.process_id)
        if proc is not None and not proc.is_terminal:
            req = EscalationRequest(
                id=req.id, process_id=req.process_id, capabilities=req.capabilities,
                reason=req.reason, status="approved", created_at=req.created_at,
                resolved_at=time.time(), resolved_by=by,
                target="process",
            )
            self._escalations[req.id] = req
            self._persist_escalation(req)
            merged = sorted(set(proc.capabilities or []) | set(req.capabilities))
            proc.capabilities = merged
            if proc.token is not None:
                self.issue_token(req.process_id, merged)
            self._persist_process(proc)
            self._emit("escalation_approved", req.process_id, {
                "escalation_id": req.id,
                "capabilities": list(req.capabilities),
                "resolved_by": by,
                "capabilities_after": merged,
                "target": "process",
                "message": "能力已并入当前进程",
            })
            return req
        # 进程已死：并入 identity 编制档案
        applied = await self._approve_escalation_to_identity(req, by=by)
        if applied is not None:
            return applied
        raise ValueError(
            "进程已终止且无法解析所属身份——请拒绝该提权，或确认 identity 层已启用"
        )

    async def _approve_escalation_to_identity(
        self, req: EscalationRequest, *, by: str
    ) -> EscalationRequest | None:
        """死进程提权：能力写入 AgentIdentity.capabilities（编制层）。"""
        reg = self.identity_registry
        if reg is None:
            return None
        identity_id = await self._resolve_identity_for_process(req.process_id)
        if identity_id is None:
            return None
        ident = await reg.get(identity_id)
        if ident is None:
            return None
        merged = sorted(set(ident.capabilities or []) | set(req.capabilities))
        await reg.set_capabilities(identity_id, merged, by=f"escalation:{by}")
        done = EscalationRequest(
            id=req.id, process_id=req.process_id, capabilities=req.capabilities,
            reason=req.reason, status="approved", created_at=req.created_at,
            resolved_at=time.time(), resolved_by=by,
            target="identity",
            identity_id=str(identity_id),
        )
        self._escalations[done.id] = done
        self._persist_escalation(done)
        self._emit("escalation_approved", req.process_id, {
            "escalation_id": done.id,
            "capabilities": list(done.capabilities),
            "resolved_by": by,
            "capabilities_after": merged,
            "target": "identity",
            "identity_id": str(identity_id),
            "message": "能力已并入身份编制档案，下次派活生效",
        })
        return done

    async def _resolve_identity_for_process(self, process_id: str) -> Any | None:
        """从内存/Redis 进程或 DB 档案解析 identity id。"""
        proc = self._resolve_process(process_id)
        name = None
        if proc is not None:
            name = getattr(proc, "identity", None) or (proc.to_dict() or {}).get("identity")
        try:
            from sqlalchemy import select

            from backend.database import AsyncSessionLocal
            from backend.models.agent_identity import AgentIdentity, KernelProcessRecord

            async with AsyncSessionLocal() as session:
                row = (
                    await session.execute(
                        select(KernelProcessRecord).where(
                            KernelProcessRecord.process_id == process_id
                        )
                    )
                ).scalar_one_or_none()
                if row is not None:
                    if row.identity_id is not None:
                        return row.identity_id
                    name = name or row.identity_key
                if name:
                    ident = (
                        await session.execute(
                            select(AgentIdentity).where(AgentIdentity.name == name)
                        )
                    ).scalar_one_or_none()
                    if ident is not None:
                        return ident.id
        except Exception as e:
            logger.warning("resolve identity for process failed: %s", e)
        return None

    async def deny_escalation(self, request_id: str, *, by: str = "user") -> EscalationRequest:
        """拒绝提权。不要求进程仍存活（重启后仍可清掉 DB pending）。"""
        req = self._escalations.get(request_id)
        if req is None:
            raise ValueError(f"未知提权申请 {request_id}")
        if req.status != "pending":
            raise ValueError(f"申请已处理（{req.status}）")
        req = EscalationRequest(
            id=req.id, process_id=req.process_id, capabilities=req.capabilities,
            reason=req.reason, status="denied", created_at=req.created_at,
            resolved_at=time.time(), resolved_by=by,
        )
        self._escalations[req.id] = req
        self._persist_escalation(req)
        self._emit("escalation_denied", req.process_id, {
            "escalation_id": req.id,
            "capabilities": list(req.capabilities),
            "resolved_by": by,
        })
        return req

    async def ensure_escalation_loaded(self, request_id: str) -> EscalationRequest | None:
        """跨 worker：Redis → DB 水合到内存。

        **总是**尝试远端刷新（禁止「内存已有就短路」——否则他 worker
        已批准/拒绝后本机仍卡在陈旧 pending）。
        """
        # Redis 优先（低延迟）
        if self._shared is not None:
            try:
                data = self._shared.get_escalation(request_id)
                if data:
                    self.hydrate_escalation(data)
                    return self._escalations.get(request_id)
            except Exception as e:
                logger.warning("load escalation from Redis failed: %s", e)
        try:
            from sqlalchemy import select

            from backend.database import AsyncSessionLocal
            from backend.models.agent_identity import KernelEscalationRecord

            async with AsyncSessionLocal() as session:
                row = (
                    await session.execute(
                        select(KernelEscalationRecord).where(
                            KernelEscalationRecord.escalation_id == request_id
                        )
                    )
                ).scalar_one_or_none()
            if row is None:
                return self._escalations.get(request_id)
            self.hydrate_escalation({
                "id": row.escalation_id,
                "process_id": row.process_id,
                "capabilities": row.capabilities or [],
                "reason": row.reason or "",
                "status": row.status,
                "created_at": row.created_at_ts or 0,
                "resolved_at": row.resolved_at,
                "resolved_by": row.resolved_by,
            })
            return self._escalations.get(request_id)
        except Exception as e:
            logger.warning("load escalation from DB failed: %s", e)
            return self._escalations.get(request_id)

    def list_escalations(self, *, status: str | None = None) -> list[EscalationRequest]:
        if self._shared is not None:
            try:
                for d in self._shared.list_pending_escalations():
                    self.hydrate_escalation(d)
            except Exception as e:
                logger.warning("list_escalations redis merge: %s", e)
        out = list(self._escalations.values())
        if status is not None:
            out = [r for r in out if r.status == status]
        return sorted(out, key=lambda r: r.created_at, reverse=True)

    def _persist_escalation(self, req: EscalationRequest) -> None:
        """提权外部化：DB sink + Redis（多 worker 可读 pending）。"""
        if self._persistence_sink is not None:
            try:
                self._persistence_sink({
                    "op": "escalation_upsert",
                    "data": req.to_dict(),
                })
            except Exception as e:
                logger.warning("kernel escalation sink 失败（不阻断）: %s", e)
        if self._shared is not None:
            try:
                self._shared.put_escalation(req.to_dict())
            except Exception as e:
                logger.warning("kernel Redis put_escalation 失败（不阻断）: %s", e)

    def hydrate_escalation(self, data: dict[str, Any]) -> None:
        """从 DB/Redis 恢复或**刷新**一条提权到内存（upsert，覆盖陈旧 status）。"""
        eid = str(data.get("id") or data.get("escalation_id") or "")
        if not eid:
            return
        caps = data.get("capabilities") or []
        if isinstance(caps, str):
            caps = [caps]
        self._escalations[eid] = EscalationRequest(
            id=eid,
            process_id=str(data.get("process_id") or ""),
            capabilities=tuple(caps),
            reason=str(data.get("reason") or ""),
            status=str(data.get("status") or "pending"),
            created_at=float(data.get("created_at") or data.get("created_at_ts") or 0),
            resolved_at=data.get("resolved_at"),
            resolved_by=data.get("resolved_by"),
            target=data.get("target") or None,
            identity_id=str(data["identity_id"]) if data.get("identity_id") else None,
        )

    # ── 审计 ──────────────────────────────────────────────

    def _emit(self, kind: str, process_id: str, detail: dict[str, Any]) -> KernelEvent:
        if self._events:
            prev = self._events[-1].hash
        elif self._disk_tail_hash:
            prev = self._disk_tail_hash  # 跨重启续链
        else:
            prev = _GENESIS_HASH
        eid = uuid.uuid4().hex[:16]
        ts = time.time()
        event = KernelEvent(
            kind=kind,
            process_id=process_id,
            detail=detail,
            id=eid,
            ts=ts,
            prev_hash=prev,
            hash=_event_hash(prev, kind, process_id, detail, ts, eid),
        )
        self._events.append(event)
        if len(self._events) > _EVENT_BUFFER_MAX:
            del self._events[: len(self._events) - _EVENT_BUFFER_MAX]
        if self._audit_store is not None:
            self._audit_store.append(event.to_dict())
        if self._persistence_sink is not None:
            try:
                # checkpoint 计数用（事件本体已由 audit_store JSONL 落盘）
                self._persistence_sink({"op": "event", "data": {"hash": event.hash}})
            except Exception as e:
                logger.warning("kernel 持久化 sink 失败（不阻断）: %s", e)
        # 多 worker 热缓冲：他 worker 的 GET /events 能看到本机 emit
        if self._shared is not None:
            try:
                self._shared.push_event(event.to_dict())
            except Exception as e:
                logger.debug("kernel Redis push_event 失败: %s", e)
        # 领域事件 → UI/CLI 订阅（失败不阻断审计）
        try:
            from backend.kernel.domain_events import publish_from_kernel_event

            publish_from_kernel_event(kind, process_id, detail if isinstance(detail, dict) else {})
        except Exception as e:
            logger.debug("domain_events hook: %s", e)
        logger.debug("kernel event %s proc=%s %s", kind, process_id, detail)
        return event

    def verify_event_chain(self) -> tuple[bool, int]:
        """验证事件哈希链完整性。返回 (是否完整, 首个断链位置索引或 -1)。

        注意：环形缓冲截断后，最旧事件的 prev_hash 指向已丢弃事件——
        这是合法截断不算篡改，验证从缓冲内第二条开始检查链接关系。
        """
        for i, e in enumerate(self._events):
            expected = _event_hash(e.prev_hash, e.kind, e.process_id, e.detail, e.ts, e.id)
            if e.hash != expected:
                return False, i
            if i > 0 and e.prev_hash != self._events[i - 1].hash:
                return False, i
        return True, -1

    def events(
        self,
        *,
        process_id: str | None = None,
        kind: str | None = None,
        limit: int = 200,
    ) -> list[KernelEvent]:
        """本地缓冲 + Redis 热缓冲合并（多 worker 观测）。

        Redis 侧事件不参与本机哈希链校验（他 worker 独立成链），
        仅按 id 去重后按 ts 排序返回。
        """
        by_id: dict[str, KernelEvent] = {e.id: e for e in self._events}
        if self._shared is not None:
            try:
                for d in self._shared.list_events(limit=max(limit * 2, 200)):
                    eid = str(d.get("id") or "")
                    if not eid or eid in by_id:
                        continue
                    by_id[eid] = KernelEvent(
                        kind=str(d.get("kind") or ""),
                        process_id=str(d.get("process_id") or ""),
                        detail=dict(d.get("detail") or {}),
                        id=eid,
                        ts=float(d.get("ts") or 0),
                        prev_hash=str(d.get("prev_hash") or _GENESIS_HASH),
                        hash=str(d.get("hash") or ""),
                    )
            except Exception as e:
                logger.debug("events redis merge skip: %s", e)
        out = list(by_id.values())
        if process_id is not None:
            out = [e for e in out if e.process_id == process_id]
        if kind is not None:
            out = [e for e in out if e.kind == kind]
        out.sort(key=lambda e: e.ts)
        return out[-limit:]

    def gc_terminal(self, *, older_than_seconds: float = 3600.0) -> int:
        """清理已终止进程（防内存膨胀）。返回清理数。"""
        now = time.time()
        dead = [
            pid
            for pid, p in self._processes.items()
            if p.is_terminal and p.ended_at is not None and now - p.ended_at > older_than_seconds
        ]
        for pid in dead:
            del self._processes[pid]
        return len(dead)


_kernel_singleton: AgentKernel | None = None
_kernel_persistence_singleton: Any | None = None
_kernel_shared_singleton: Any | None = None


def get_kernel() -> AgentKernel:
    """进程级单例 Kernel。

    默认挂载审计落盘（~/.takton/kernel_events.jsonl）；
    agent_kernel_audit_persist=false 可关（仅内存缓冲）。
    0.5：默认装配持久化 sink + 身份注册表（agent_kernel_persistence=false 可关）。
    多 worker：agent_kernel_redis_shared + redis_url → Redis 共享 mediate/进程/提权。
    """
    global _kernel_singleton, _kernel_persistence_singleton, _kernel_shared_singleton
    if _kernel_singleton is None:
        store = None
        persistence = None
        shared = None
        try:
            from backend.core.config import settings

            if bool(getattr(settings, "agent_kernel_audit_persist", True)):
                from backend.kernel.audit_store import AuditEventStore

                path = str(getattr(settings, "agent_kernel_audit_path", "") or "") or None
                store = AuditEventStore(path)
        except Exception as e:
            logger.warning("kernel 审计落盘初始化失败（仅内存缓冲）: %s", e)
        try:
            from backend.core.config import settings as _s

            if bool(getattr(_s, "agent_kernel_persistence", True)):
                from backend.database import AsyncSessionLocal
                from backend.kernel.persistence import KernelPersistence

                persistence = KernelPersistence(
                    AsyncSessionLocal,
                    store,
                    checkpoint_interval=int(getattr(_s, "agent_kernel_checkpoint_interval", 500)),
                )
        except Exception as e:
            logger.warning("kernel 持久化初始化失败（仅内存态）: %s", e)
            persistence = None
        try:
            from backend.kernel.shared_store import create_shared_store_from_settings

            shared = create_shared_store_from_settings()
        except Exception as e:
            logger.warning("kernel Redis 共享初始化失败: %s", e)
            shared = None
        _kernel_persistence_singleton = persistence
        _kernel_shared_singleton = shared
        _kernel_singleton = AgentKernel(
            audit_store=store,
            persistence_sink=persistence.sink() if persistence is not None else None,
            shared_store=shared,
        )
        if persistence is not None:
            try:
                from backend.database import AsyncSessionLocal
                from backend.kernel.identity import IdentityRegistry

                _kernel_singleton.identity_registry = IdentityRegistry(
                    _kernel_singleton, AsyncSessionLocal
                )
            except Exception as e:
                logger.warning("kernel 身份注册表初始化失败: %s", e)
    return _kernel_singleton


def get_kernel_persistence() -> Any | None:
    """0.5 持久化协调器（lifespan 用它 recover + 拉 worker）。"""
    get_kernel()  # 确保已装配
    return _kernel_persistence_singleton


def get_kernel_shared_store() -> Any | None:
    """多 worker Redis 共享态（未启用时 None）。"""
    get_kernel()
    return _kernel_shared_singleton


def reset_kernel_for_tests() -> None:
    global _kernel_singleton, _kernel_persistence_singleton, _kernel_shared_singleton
    _kernel_singleton = None
    _kernel_persistence_singleton = None
    _kernel_shared_singleton = None
