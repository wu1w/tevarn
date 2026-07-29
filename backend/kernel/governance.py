"""可研究级治理骨架：红线、策略预设、内核面导出。

与审批规则（approval_rules）互补：
- approval_rules = 运行时开关（自动放行低风险等）
- 本模块 = 不可关闭的制度红线 + 研究/审计用的 kernel surface 清单
"""

from __future__ import annotations

import time
from typing import Any

from backend.kernel.protocol_spec import (
    LEGACY_TERM_MAP,
    PRODUCT_CONCEPTS,
    PROTOCOL_NAME,
    PROTOCOL_VERSION,
)

# ── 制度红线（enforced=True 表示实现层有硬约束，非文案）──────────

GOVERNANCE_RED_LINES: list[dict[str, Any]] = [
    {
        "id": "capability_narrow_only",
        "title_zh": "能力只能单调收窄",
        "title_en": "Capabilities only narrow",
        "enforced": True,
        "mechanism": "CapabilityToken.narrow + CapabilityEscalationError",
        "product_concept": "approval",
    },
    {
        "id": "escalation_only_widen",
        "title_zh": "提权是唯一合法扩大通道",
        "title_en": "Escalation is the only widen path",
        "enforced": True,
        "mechanism": "request_escalation → human approve/deny",
        "product_concept": "approval",
    },
    {
        "id": "evolution_human_approval",
        "title_zh": "进化建议永不自动应用编制 caps",
        "title_en": "Evolution never auto-applies caps",
        "enforced": True,
        "mechanism": "EvolutionEngine auto_apply=False; proposals need approve",
        "product_concept": "approval",
    },
    {
        "id": "workforce_no_owner_flood",
        "title_zh": "员工工单工具不刷主人确认洪水",
        "title_en": "Workforce jobs do not flood owner confirms",
        "enforced": True,
        "mechanism": "steward_permission + Identity.capabilities mediate",
        "product_concept": "job",
    },
    {
        "id": "inbox_bounded",
        "title_zh": "工单队列有界",
        "title_en": "Inbox queue is bounded",
        "enforced": True,
        "mechanism": "max_pending + overflow drop oldest pending",
        "product_concept": "job",
    },
    {
        "id": "audit_hash_chain",
        "title_zh": "内核事件哈希链",
        "title_en": "Kernel events on hash chain",
        "enforced": True,
        "mechanism": "KernelEvent prev_hash / audit_store",
        "product_concept": "approval",
    },
    {
        "id": "single_user_default",
        "title_zh": "默认单用户，0.6 前不做公有多租户",
        "title_en": "Single-user default; no multi-tenant before 0.6+",
        "enforced": False,  # 产品策略，非运行时硬拦
        "mechanism": "config / icebox",
        "product_concept": "employee",
    },
]

# 策略预设：宽松但可见 vs 锁死
POLICY_PRESETS: dict[str, dict[str, Any]] = {
    "relaxed_visible": {
        "id": "relaxed_visible",
        "title_zh": "宽松但提权可见",
        "title_en": "Relaxed but escalations visible",
        "approval_rules": {
            "auto_low_risk": True,
            "review_high_risk": True,
            "review_capability_upgrade": True,
            "review_evolution": True,
            "auto_tighten_2x": True,
        },
        "notes_zh": "低风险自动；高危与扩权/进化必审。适合主人在家盯一眼。",
    },
    "locked": {
        "id": "locked",
        "title_zh": "锁死",
        "title_en": "Locked down",
        "approval_rules": {
            "auto_low_risk": False,
            "review_high_risk": True,
            "review_capability_upgrade": True,
            "review_evolution": True,
            "auto_tighten_2x": True,
        },
        "notes_zh": "几乎一切敏感操作进人审；适合不信任模型/离家更久。",
    },
}

# 可研究级 kernel surface（分层，非完整 OpenAPI）
KERNEL_SURFACE: dict[str, list[dict[str, str]]] = {
    "kernel": [
        {"name": "create_process", "role": "lifecycle"},
        {"name": "end_process", "role": "lifecycle"},
        {"name": "mediate", "role": "policy"},
        {"name": "charge_tokens", "role": "budget"},
        {"name": "request_escalation", "role": "governance"},
        {"name": "events / hash chain", "role": "audit"},
    ],
    "identity": [
        {"name": "create / list / suspend", "role": "crew"},
        {"name": "capabilities + budget", "role": "policy"},
        {"name": "append_memory / current_memory", "role": "memory"},
    ],
    "inbox": [
        {"name": "enqueue / claim_next", "role": "scheduling"},
        {"name": "complete / fail / cancel", "role": "lifecycle"},
        {"name": "reclaim_stale / dead / requeue", "role": "durability"},
    ],
    "dispatcher": [
        {"name": "tick", "role": "scheduling"},
        {"name": "cancel_job", "role": "control"},
        {"name": "global concurrency cap", "role": "reliability"},
    ],
    "protocol": [
        {"name": "agent_card export", "role": "interop"},
        {"name": "a2a task envelope", "role": "interop"},
        {"name": "governance export", "role": "research"},
    ],
}


def build_governance_export(
    *,
    include_live_rules: bool = False,
    live_rules: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """导出治理清单（研究/审计/对齐 Agent-OS 论文需求用）。"""
    out: dict[str, Any] = {
        "kind": "governance_manifest",
        "protocol": PROTOCOL_NAME,
        "protocol_version": PROTOCOL_VERSION,
        "product_concepts": PRODUCT_CONCEPTS,
        "legacy_term_map": LEGACY_TERM_MAP,
        "red_lines": list(GOVERNANCE_RED_LINES),
        "policy_presets": POLICY_PRESETS,
        "kernel_surface": KERNEL_SURFACE,
        "invariants": {
            "auto_apply_evolution_caps": False,
            "capability_monotonic_narrow": True,
            "human_approval_for_widen": True,
            "inbox_max_attempts_then_dead": True,
        },
        "ts": time.time(),
    }
    if include_live_rules:
        out["live_approval_rules"] = live_rules
    return out


def build_kernel_surface_export() -> dict[str, Any]:
    """研究级内核面：分层能力 + 协议入口 + 心智。"""
    return {
        "kind": "kernel_surface",
        "protocol": PROTOCOL_NAME,
        "protocol_version": PROTOCOL_VERSION,
        "layers": KERNEL_SURFACE,
        "product_concepts": PRODUCT_CONCEPTS,
        "red_line_ids": [r["id"] for r in GOVERNANCE_RED_LINES if r.get("enforced")],
        "research_notes": {
            "zh": (
                "本 surface 描述「单用户数字班子 OS」控制面，"
                "对齐 Agent-OS 论文中的 lifecycle/memory/tools/orchestration/"
                "observability/safety/governance 子集；不含 HRT 与多租户。"
            ),
            "en": (
                "Control plane for a single-user digital crew OS; "
                "subset of Agent-OS requirements without HRT or multi-tenancy."
            ),
        },
        "ts": time.time(),
    }


def red_line_by_id(line_id: str) -> dict[str, Any] | None:
    for r in GOVERNANCE_RED_LINES:
        if r["id"] == line_id:
            return dict(r)
    return None


def assert_research_invariants() -> list[str]:
    """静态自检：返回问题列表（空=通过）。可供测试调用。"""
    problems: list[str] = []
    ids = [r["id"] for r in GOVERNANCE_RED_LINES]
    if len(ids) != len(set(ids)):
        problems.append("duplicate red_line ids")
    for required in (
        "capability_narrow_only",
        "escalation_only_widen",
        "evolution_human_approval",
        "inbox_bounded",
    ):
        if required not in ids:
            problems.append(f"missing red_line {required}")
    if "employee" not in PRODUCT_CONCEPTS or "job" not in PRODUCT_CONCEPTS:
        problems.append("product_concepts incomplete")
    if "relaxed_visible" not in POLICY_PRESETS or "locked" not in POLICY_PRESETS:
        problems.append("policy presets incomplete")
    return problems
