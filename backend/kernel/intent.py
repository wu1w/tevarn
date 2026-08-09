"""Intent Declaration —— 意图 → 最小权限（P0-B）。

.. deprecated:: P0-B
    **权威合成在 Rust** ``tevarn_kernel::intent`` / host ``apply_intent``。
    ``apply_intent_to_process`` 优先走 Rust RPC；失败才用本文件本地逻辑（fallback）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from backend.kernel.capability import CapabilityToken

# 默认可自由授予的安全能力（只读类）
DEFAULT_GRANTABLE: frozenset[str] = frozenset({
    "file_read", "grep", "glob", "web_search", "web_extract",
    "session_search", "memory", "knowledge_search", "wiki_search",
    # 与 Rust intent::DEFAULT_GRANTABLE 对齐（主会话编制/目标/技能）
    "crew_steward", "clarify", "use_tool_pack", "current_time",
    "okr_goal", "manage_goal", "autopilot", "manage_skill", "manage_mcp",
})

# 高危能力：需 constraints.allow_risky=True 显式接受
RISKY_CAPABILITIES: frozenset[str] = frozenset({
    "terminal", "file_write", "file_edit", "browser", "computer",
    "delegate_task", "cronjob", "send_message",
})


@dataclass(frozen=True)
class IntentDeclaration:
    goal: str
    capabilities: tuple[str, ...] = ()
    constraints: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IntentDeclaration":
        if not isinstance(data, dict):
            raise ValueError("intent declaration 必须是 dict")
        goal = str(data.get("goal") or "").strip()
        if not goal:
            raise ValueError("intent declaration 缺少 goal")
        caps = data.get("capabilities") or []
        if not isinstance(caps, (list, tuple)):
            raise ValueError("capabilities 必须是列表")
        constraints = data.get("constraints") or {}
        if not isinstance(constraints, dict):
            raise ValueError("constraints 必须是 dict")
        return cls(
            goal=goal,
            capabilities=tuple(str(c) for c in caps),
            constraints=dict(constraints),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "capabilities": list(self.capabilities),
            "constraints": self.constraints,
        }


def synthesize_capabilities(
    intent: IntentDeclaration,
    *,
    grantable: frozenset[str] = DEFAULT_GRANTABLE,
    risky: frozenset[str] = RISKY_CAPABILITIES,
) -> tuple[list[str], list[str]]:
    """按策略合成能力集。返回 (granted, dropped)。

    规则：
    - grantable 内的能力直接授予
    - risky 内的能力仅当 constraints.allow_risky=True 时授予，否则剔除
    - 既不在 grantable 也不在 risky 的未知能力：剔除（白名单原则，宁缺毋滥）
    - 未声明任何能力：授予 grantable 全集（只读探索是安全的默认）
    """
    allow_risky = bool(intent.constraints.get("allow_risky"))
    requested = list(intent.capabilities) if intent.capabilities else sorted(grantable)

    granted: list[str] = []
    dropped: list[str] = []
    for cap in requested:
        if cap in grantable:
            granted.append(cap)
        elif cap in risky:
            (granted if allow_risky else dropped).append(cap)
        else:
            dropped.append(cap)
    return granted, dropped


def synthesize_token(
    intent: IntentDeclaration,
    *,
    parent_token: CapabilityToken | None = None,
    process_id: str = "",
    grantable: frozenset[str] = DEFAULT_GRANTABLE,
    risky: frozenset[str] = RISKY_CAPABILITIES,
) -> tuple[CapabilityToken, list[str]]:
    """声明 → 能力令牌。有父令牌时 narrow 到父集子集（可能进一步缩小 granted）。"""
    granted, dropped = synthesize_capabilities(intent, grantable=grantable, risky=risky)

    ttl = intent.constraints.get("ttl_seconds")
    expires_at = time.time() + float(ttl) if ttl else None

    if parent_token is not None:
        # 父令牌不允许的能力从 granted 移到 dropped（narrowing 会抛异常，先过滤）
        if "*" not in parent_token.capabilities:
            still: list[str] = []
            for cap in granted:
                (still if cap in parent_token.capabilities else dropped).append(cap)
            granted = still
        token = parent_token.narrow(granted, process_id=process_id, expires_at=expires_at)
        return token, dropped

    return (
        CapabilityToken(
            capabilities=frozenset(granted),
            process_id=process_id,
            expires_at=expires_at,
        ),
        dropped,
    )


def apply_intent_to_process(
    kernel: Any,
    process_id: str,
    intent: IntentDeclaration | dict[str, Any],
    *,
    parent_token: CapabilityToken | None = None,
    grantable: frozenset[str] = DEFAULT_GRANTABLE,
    risky: frozenset[str] = RISKY_CAPABILITIES,
) -> tuple[CapabilityToken, list[str]]:
    """生产路径：声明 → 合成令牌 → 挂载进程。优先 Rust host RPC。"""
    if isinstance(intent, dict):
        decl = IntentDeclaration.from_dict(intent)
    elif isinstance(intent, IntentDeclaration):
        decl = intent
    else:
        raise TypeError("intent 须为 IntentDeclaration 或 dict")

    # Rust client path
    apply_rpc = getattr(kernel, "apply_intent", None)
    if callable(apply_rpc) and hasattr(kernel, "_call"):
        try:
            return apply_rpc(process_id, decl.to_dict(), parent_token=parent_token)
        except TypeError:
            # RustAgentKernel.apply_intent signature below
            pass
        try:
            return kernel.apply_intent(  # type: ignore[misc]
                process_id, decl.to_dict(), parent_token=parent_token
            )
        except Exception:
            pass

    resolve = getattr(kernel, "_resolve_process", None)
    if resolve is None:
        resolve = getattr(kernel, "get_process", None)
    proc = resolve(process_id) if callable(resolve) else None
    if proc is None and hasattr(kernel, "get_process"):
        proc = kernel.get_process(process_id)
    if proc is None:
        raise ValueError(f"未知进程 {process_id}")

    ptok = parent_token
    if ptok is None and getattr(proc, "parent_id", None):
        parent = None
        if hasattr(kernel, "get_process"):
            parent = kernel.get_process(proc.parent_id)
        if parent is not None:
            ptok = getattr(parent, "token", None)

    token, dropped = synthesize_token(
        decl,
        parent_token=ptok,
        process_id=process_id,
        grantable=grantable,
        risky=risky,
    )
    granted = sorted(token.capabilities)
    if proc.capabilities is not None or granted:
        proc.capabilities = granted
    proc.token = token
    proc.meta = dict(proc.meta or {})
    proc.meta["intent"] = decl.to_dict()
    proc.meta["intent_dropped"] = list(dropped)
    share = getattr(kernel, "_share_process", None)
    if callable(share):
        share(proc)
    persist = getattr(kernel, "_persist_process", None)
    if callable(persist):
        try:
            persist(proc)
        except Exception:
            pass
    return token, dropped
