"""Intent Declaration —— 意图声明 → 最小权限能力合成（阶段 2 雏形）。

思想来源：agent 不该带着全量能力出发，而是先声明「我要干什么」，
Kernel 按声明 + 策略合成**最小够用**的临时能力集。

W1-W3 已有：进程能力集 / 令牌 narrowing / mediate 强制。
本模块补上「声明 → 合成」这一环：

    intent = IntentDeclaration.from_dict({
        "goal": "读取源码并总结架构",
        "capabilities": ["file_read", "grep", "glob"],
        "constraints": {"token_budget": 20000, "ttl_seconds": 3600},
    })
    token, dropped = synthesize_token(intent, kernel=kernel, process_id=proc.id)
    # dropped = 被策略剔除的能力（高危且未显式接受风险）

安全语义：
- 高危能力（默认 RISKY_CAPABILITIES）必须 constraints.allow_risky=True 才授予，
  否则剔除并返回在 dropped 中（调用方可告知用户/agent 缺口）。
- 有父令牌时自动 narrow（能力单调递减）。
- TTL 生成过期令牌（W2 已强制过期检查）。
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
