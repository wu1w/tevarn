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

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "process_id": self.process_id,
            "capabilities": list(self.capabilities),
            "reason": self.reason,
            "status": self.status,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "resolved_by": self.resolved_by,
        }


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
        """本地缓存 + Redis 权威合并（多 worker mediate 入口）。"""
        if self._shared is not None:
            try:
                data = self._shared.get_process(process_id)
            except Exception as e:
                logger.warning("kernel Redis get_process 失败: %s", e)
                data = None
            if data is not None:
                local = self._processes.get(process_id)
                if local is None:
                    local = AgentProcess.from_dict(data)
                    self._processes[process_id] = local
                else:
                    # 刷新跨 worker 可变字段
                    local.capabilities = data.get("capabilities")
                    local.tokens_used = int(data.get("tokens_used") or 0)
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

        proc = AgentProcess(
            identity=identity,
            session_id=session_id,
            parent_id=parent_id,
            capabilities=effective_caps,
            token_budget=effective_budget,
            meta=dict(meta or {}),
        )
        self._processes[proc.id] = proc
        self._emit("process_created", proc.id, {
            "identity": identity,
            "session_id": session_id,
            "parent_id": parent_id,
            "capabilities": effective_caps,
            "token_budget": effective_budget,
        })
        self._persist_process(proc)
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

    # ── 执行中介 ──────────────────────────────────────────────

    async def mediate(
        self,
        process_id: str,
        action: MediationAction,
        target: str,
        args: dict[str, Any] | None = None,
    ) -> MediationDecision:
        """所有执行动作的统一入口（W3 全路径收口）。

        W1 语义：
        - 进程已终止 → 拒绝
        - 显式能力集进程：target 不在能力集内 → 拒绝（KernelPermissionError）
        - 兼容模式（capabilities=None）：放行 + 记录
        多 worker：进程从 Redis 水合后再裁决（能力集/令牌跨进程一致）。
        """
        proc = self._resolve_process(process_id)
        if proc is None:
            decision = MediationDecision(False, f"未知进程 {process_id}", capability_checked=True)
            self._emit("mediation", process_id, {
                "action": action, "target": target, "allowed": False, "reason": decision.reason,
            })
            raise KernelPermissionError(decision.reason, decision)

        if proc.is_terminal:
            decision = MediationDecision(False, f"进程已终止（{proc.state}）", capability_checked=True)
            self._emit("mediation", proc.id, {
                "action": action, "target": target, "allowed": False, "reason": decision.reason,
            })
            raise KernelPermissionError(decision.reason, decision)

        # W2：进程持有令牌时以令牌为准——过期 / 范围外一律拒绝
        if proc.token is not None:
            if proc.token.is_expired:
                decision = MediationDecision(False, "能力令牌已过期", capability_checked=True)
                self._emit("mediation", proc.id, {
                    "action": action, "target": target, "allowed": False,
                    "reason": decision.reason, "token_id": proc.token.id,
                })
                raise KernelPermissionError(decision.reason, decision)
            if not proc.token.allows(target):
                decision = MediationDecision(
                    False, f"令牌范围不含 '{target}'（action={action}）", capability_checked=True
                )
                self._emit("mediation", proc.id, {
                    "action": action, "target": target, "allowed": False,
                    "reason": decision.reason, "token_id": proc.token.id,
                })
                raise KernelPermissionError(decision.reason, decision)
        elif proc.capabilities is not None and not proc.has_capability(target):
            decision = MediationDecision(
                False, f"能力集不含 '{target}'（action={action}）", capability_checked=True
            )
            self._emit("mediation", proc.id, {
                "action": action, "target": target, "allowed": False, "reason": decision.reason,
            })
            raise KernelPermissionError(decision.reason, decision)

        decision = MediationDecision(True, capability_checked=proc.capabilities is not None)
        self._emit("mediation", proc.id, {
            "action": action,
            "target": target,
            "allowed": True,
            "capability_checked": decision.capability_checked,
            "args_keys": sorted((args or {}).keys()),
        })
        return decision

    # ── 预算治理 ──────────────────────────────────────────────

    def charge_tokens(self, process_id: str, amount: int) -> int | None:
        """扣减进程预算，返回剩余。超限抛 BudgetExceededError（调用方决定中断策略）。

        多 worker：Redis HINCRBY 原子扣减，再同步本地缓存。
        """
        proc = self._resolve_process(process_id)
        if proc is None:
            return None
        if self._shared is not None and amount > 0:
            try:
                used, remaining = self._shared.charge_tokens(process_id, amount)
                if used is not None:
                    proc.tokens_used = used
                if remaining is not None and remaining <= 0:
                    self._emit("budget_exceeded", proc.id, {
                        "token_budget": proc.token_budget,
                        "tokens_used": proc.tokens_used,
                    })
                    self._share_process(proc)
                    raise BudgetExceededError(
                        f"进程 {process_id} 预算耗尽（{proc.tokens_used}/{proc.token_budget}）"
                    )
                return remaining
            except BudgetExceededError:
                raise
            except Exception as e:
                logger.warning("redis charge_tokens 失败，回退本地: %s", e)
        remaining = proc.charge_tokens(amount)
        self._share_process(proc)
        if remaining is not None and remaining <= 0:
            self._emit("budget_exceeded", proc.id, {
                "token_budget": proc.token_budget,
                "tokens_used": proc.tokens_used,
            })
            raise BudgetExceededError(
                f"进程 {process_id} 预算耗尽（{proc.tokens_used}/{proc.token_budget}）"
            )
        return remaining

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

        兼容模式进程（capabilities=None）本就全放行，申请无意义——拒绝。"""
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
        req = EscalationRequest(
            id=uuid.uuid4().hex[:16],
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
        # 审批规则：低风险 + auto_low_risk 开启 → 自动批准（不打扰老板）
        try:
            from backend.kernel.approval_rules import should_auto_approve_escalation

            if await should_auto_approve_escalation(list(caps)):
                return await self.approve_escalation(req.id, by="auto:approval_rules")
        except Exception as e:
            logger.debug("auto-approve check skipped: %s", e)
        return req

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
        proc = self._processes.get(req.process_id)
        proc = self._resolve_process(req.process_id)
        if proc is not None and not proc.is_terminal:
            req = EscalationRequest(
                id=req.id, process_id=req.process_id, capabilities=req.capabilities,
                reason=req.reason, status="approved", created_at=req.created_at,
                resolved_at=time.time(), resolved_by=by,
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
        })
        return done

    async def _resolve_identity_for_process(self, process_id: str) -> Any | None:
        """从内存进程或 DB 档案解析 identity id。"""
        proc = self._processes.get(process_id)
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
        """跨 worker：Redis → DB 水合到内存。"""
        if request_id in self._escalations:
            return self._escalations[request_id]
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
                return None
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
            return None

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
        """从 DB 恢复一条提权到内存（recover 用；不重复 emit）。"""
        eid = str(data.get("id") or data.get("escalation_id") or "")
        if not eid or eid in self._escalations:
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
        out = self._events
        if process_id is not None:
            out = [e for e in out if e.process_id == process_id]
        if kind is not None:
            out = [e for e in out if e.kind == kind]
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
