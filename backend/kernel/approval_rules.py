"""审批规则（settings KV「approval_rules」）——真正被内核消费。

与前端 Approvals 页 RulesModal 同源 key：
  auto_low_risk / review_high_risk / review_capability_upgrade /
  review_evolution / auto_tighten_2x

规则只影响「是否自动放行 / 是否必须人工」；
进化建议（evolution）永不自动应用（硬红线，本模块不提供后门）。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 最近一次成功加载的规则（sync 路径 / charge_tokens 用）
_RULES_CACHE: list[dict[str, Any]] | None = None

# 默认与前端 DEFAULT_RULES 对齐
DEFAULT_RULES: list[dict[str, Any]] = [
    {"key": "auto_low_risk", "enabled": True},
    {"key": "review_high_risk", "enabled": True, "warn": True},
    {"key": "review_capability_upgrade", "enabled": True, "warn": True},
    {"key": "review_evolution", "enabled": True, "warn": True},
    {"key": "auto_tighten_2x", "enabled": True},
]

# 低风险能力（可自动放行候选）
_LOW_RISK_CAPS = frozenset({
    "web_search", "web_extract", "search", "read", "file_read",
    "glob", "grep", "list_dir", "memory_read", "wiki_read",
    "knowledge_query", "rag_query",
})

# 高危能力（永远不自动放行）
_DANGER_CAPS = frozenset({
    "command", "shell", "bash", "exec", "file_rw", "file_write",
    "write", "delete", "rm", "sudo", "network", "egress", "browser",
    "http", "mcp", "subagent", "spawn",
})


def _rule_enabled(rules: list[dict[str, Any]], key: str, default: bool = True) -> bool:
    for r in rules:
        if r.get("key") == key:
            return bool(r.get("enabled", default))
    return default


async def load_approval_rules() -> list[dict[str, Any]]:
    """从 settings 表读 approval_rules；失败回落默认。"""
    global _RULES_CACHE
    try:
        from sqlalchemy import select

        from backend.database import AsyncSessionLocal
        from backend.models.setting import Setting

        async with AsyncSessionLocal() as session:
            row = (
                await session.execute(
                    select(Setting).where(Setting.key == "approval_rules")
                )
            ).scalar_one_or_none()
        if row is None or row.value is None:
            _RULES_CACHE = list(DEFAULT_RULES)
            return _RULES_CACHE
        val = row.value
        if isinstance(val, str):
            import json
            val = json.loads(val)
        if isinstance(val, list) and val:
            _RULES_CACHE = val
            return val
    except Exception as e:
        logger.debug("load approval_rules failed: %s", e)
    _RULES_CACHE = list(DEFAULT_RULES)
    return _RULES_CACHE


def rule_enabled_sync(key: str, default: bool = True) -> bool:
    """同步读缓存规则（charge_tokens 等零 await 路径）。"""
    rules = _RULES_CACHE if _RULES_CACHE is not None else DEFAULT_RULES
    return _rule_enabled(rules, key, default)


def classify_caps(capabilities: list[str] | tuple[str, ...] | set[str]) -> str:
    """返回 low / high / upgrade。R4：优先 Rust approval_classify。"""
    caps_list = [str(c) for c in capabilities]
    try:
        from backend.kernel import get_kernel

        k = get_kernel()
        if hasattr(k, "_call"):
            # sync rules to rust best-effort
            rules = _RULES_CACHE if _RULES_CACHE is not None else DEFAULT_RULES
            try:
                k._call("approval_set_rules", {"rules": rules})
            except Exception:
                pass
            r = k._call("approval_classify", {"capabilities": caps_list}) or {}
            kind = str(r.get("kind") or "")
            if kind in ("low", "high", "upgrade"):
                return kind
    except Exception as e:
        logger.debug("approval_classify rust skip: %s", e)
    caps = {c.lower() for c in capabilities}
    if any(
        any(d in c for d in _DANGER_CAPS) or c in _DANGER_CAPS
        for c in caps
    ):
        return "high"
    if caps and caps <= _LOW_RISK_CAPS:
        return "low"
    # 部分命中 low、或未知能力 → 视为能力升级
    return "upgrade"


async def should_auto_approve_escalation(
    capabilities: list[str] | tuple[str, ...],
) -> bool:
    """提权是否可自动批准（仅 auto_low_risk + 纯低风险能力）。"""
    rules = await load_approval_rules()
    # push rules + ask rust
    try:
        from backend.kernel import get_kernel

        k = get_kernel()
        if hasattr(k, "_call"):
            k._call("approval_set_rules", {"rules": rules})
            r = k._call(
                "approval_should_auto",
                {"capabilities": [str(c) for c in capabilities]},
            ) or {}
            if "auto_approve" in r:
                return bool(r.get("auto_approve"))
    except Exception as e:
        logger.debug("approval_should_auto rust skip: %s", e)
    if not _rule_enabled(rules, "auto_low_risk", True):
        return False
    kind = classify_caps(capabilities)
    if kind == "high" and _rule_enabled(rules, "review_high_risk", True):
        return False
    if kind == "upgrade" and _rule_enabled(rules, "review_capability_upgrade", True):
        return False
    return kind == "low"


def evolution_requires_review() -> bool:
    """进化建议是否必须人工（硬默认 True；规则关闭也仍强制——双保险）。"""
    return True  # 永不因规则关闭而自动应用
