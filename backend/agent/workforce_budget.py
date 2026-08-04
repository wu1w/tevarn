"""Per-job token budget lift for workforce runs.

Identity default budgets (often 30k) are too tight for audit/research with
multi-file reads. Lift at *process* creation time without mutating the
identity row — identity default remains the floor, task class raises the
ceiling for this job only.
"""

from __future__ import annotations

import os
import re
from typing import Any

# Floor by primary task kind (tokens). Tunable via env multipliers later.
_KIND_FLOOR: dict[str, int] = {
    "audit": 150_000,
    "health_check": 180_000,
    "diagnose": 120_000,
    # PR2: research workers capped lower (was 100k; thrash burned 180–310k)
    "research": 80_000,
    "data_stats": 100_000,
    "compare": 100_000,
    "doc_qa": 80_000,
    "fix": 100_000,
    "build": 100_000,
    "cite_fact": 80_000,
    "math": 40_000,
    "inventory": 60_000,
    "find": 50_000,
}

# Role/name hints when instruction classify is weak
_AUDIT_ROLE = re.compile(r"(审计|audit|security\s*review|核验|巡检|体检|健康检查)", re.I)
_HEALTH_INSTR = re.compile(
    r"(健康检查|系统体检|全量巡检|health\s*check|full\s*scan|"
    r"前端.*检查|后端.*检查|kernel.*检查|测试套件|集成健康)",
    re.I,
)
_MULTI_SECTION = re.compile(r"(###\s*任务|##\s*任务|\n\s*\d+[\.、]\s)", re.M)

# 默认硬顶 200 万（原 50 万导致长审计/PPT 必撞墙）；环境可覆盖
_HARD_CAP = 2_000_000
_DEFAULT_FALLBACK = 100_000
# 单条 instruction 过大 → 建议拆单（仍允许执行但抬预算 + 提示）
_OVERSIZE_CHARS = 2_500
_OVERSIZE_SECTIONS = 3


def _env_int(name: str, default: int) -> int:
    try:
        v = int(os.environ.get(name) or default)
        return v if v > 0 else default
    except Exception:
        return default


def _settings_hard_cap() -> int:
    try:
        from backend.core.config import settings

        cap = int(getattr(settings, "agent_workforce_budget_hard_cap", 0) or 0)
        if cap > 0:
            return cap
    except Exception:
        pass
    return _env_int("TAKTON_WORKFORCE_BUDGET_HARD_CAP", _HARD_CAP)


def kind_budget_floor(kind: str | None) -> int:
    if not kind:
        return 0
    base = _KIND_FLOOR.get(kind, 0)
    mult = float(os.environ.get("TAKTON_BUDGET_LIFT_MULT") or "1")
    try:
        mult = max(0.5, min(mult, 3.0))
    except Exception:
        mult = 1.0
    return int(base * mult)


def role_budget_floor(role: str | None, name: str | None = None) -> int:
    blob = f"{role or ''} {name or ''}"
    if _AUDIT_ROLE.search(blob):
        return kind_budget_floor("audit")
    return 0


def is_budget_exceeded_result(text: str | None) -> bool:
    """Detect *token* budget kill — NOT iteration-budget grace/exhaust.

    History bug: matching bare「预算耗尽」also hit「迭代预算耗尽」, so jobs that
    finished via iteration grace were marked inbox budget-fail and re-queued
    forever even with 1.5M+ token budgets still remaining.
    """
    t = text or ""
    if not t:
        return False
    low = t.lower()
    # Explicit token markers first
    if "[Budget Exceeded]" in t or "[Token 预算耗尽]" in t:
        return True
    if "kernel_token_budget_exhausted" in t or "kernel_budget_precheck" in t:
        return True
    # Iteration / round budget — complete normally, do NOT budget-fail
    if (
        "迭代预算" in t
        or "iteration budget" in low
        or "max_total" in low
        or "budget_grace" in t
        or "kernel_iteration_exhausted" in t
    ):
        return False
    # Token-specific Chinese
    if "Token 预算耗尽" in t or "进程 token" in t and ("用尽" in t or "不足" in t):
        return True
    if "token 预算" in low and any(k in t for k in ("中断", "拒绝", "耗尽", "不足")):
        return True
    if "事前预算" in t and ("不足" in t or "中断" in t):
        return True
    return False


def budget_fail_system_summary(
    *,
    instruction: str = "",
    raw: str = "",
    process_id: str | None = None,
) -> str:
    """Budget 中断后的固定短摘要 — 禁止保留模型写的长「报告框架」。"""
    instr = (instruction or "").replace("\n", " ").strip()[:160]
    pid = (process_id or "")[:12]
    # 从 raw 里抠已用/预算数字（若有）
    usage = ""
    m = re.search(r"已用\s*(\d+)\s*/\s*(\d+)", raw or "")
    if m:
        usage = f" 已用 {m.group(1)}/{m.group(2)}。"
    elif re.search(r"token_budget|tokens_used", raw or "", re.I):
        usage = " " + (raw or "")[:120]
    return (
        "[Budget Exceeded] 本工单因进程 token 预算中断，**未完成实质检查**。\n"
        f"任务摘要：{instr or '（无）'}\n"
        f"process={pid or '—'}。{usage}\n"
        "系统说明：下列不得当作「已完成报告」；请提高预算、收窄范围或拆成多张工单后重派。\n"
        "禁止模型用「报告框架/预期结果」冒充检查结论。"
    )


def instruction_size_signals(instruction: str) -> dict[str, Any]:
    """Detect oversized multi-section health/audit jobs."""
    instr = instruction or ""
    sections = len(_MULTI_SECTION.findall(instr))
    health = bool(_HEALTH_INSTR.search(instr))
    oversize = len(instr) >= _OVERSIZE_CHARS or sections >= _OVERSIZE_SECTIONS
    return {
        "chars": len(instr),
        "sections": sections,
        "health_like": health,
        "oversize": oversize,
        "should_split": oversize and (health or sections >= _OVERSIZE_SECTIONS),
    }


def split_hint_for_instruction(instruction: str) -> str | None:
    """If instruction should be split, return steward-facing hint (or None)."""
    sig = instruction_size_signals(instruction)
    if not sig["should_split"]:
        return None
    return (
        f"[budget/split] 本 instruction 过长（{sig['chars']} 字，"
        f"约 {sig['sections']} 个任务段）。建议拆成多张工单（每单一个模块/目录），"
        "或只要求「列文件+要点」禁止通读全仓。已自动抬高本单预算上限。"
    )


def suggested_token_budget(
    *,
    base: int | None,
    instruction: str = "",
    role: str | None = None,
    name: str | None = None,
) -> int | None:
    """Return process token_budget for this job.

    - base None → use workforce fallback then lift
    - base 0 → unlimited (preserve explicit 0)
    - else max(base, kind_floor, role_floor, health/oversize lift)
    """
    if base == 0:
        return 0  # explicit unlimited

    fallback = _env_int("TAKTON_WORKFORCE_FALLBACK_BUDGET", _DEFAULT_FALLBACK)
    cap = _settings_hard_cap()

    floor = int(base) if base is not None else fallback

    kind = None
    try:
        from backend.agent.task_grounding import classify_task

        kind = classify_task(instruction or "")
    except Exception:
        kind = None

    instr = instruction or ""
    sig = instruction_size_signals(instr)
    if sig["health_like"] and kind not in ("audit", "health_check"):
        kind = "health_check"

    lift = max(kind_budget_floor(kind), role_budget_floor(role, name))
    # Multi-label / long instruction — 长工单自动抬高，减少开局就顶死
    if len(instr) > 600 and lift < 100_000:
        lift = max(lift, 100_000)
    if len(instr) > 1200:
        lift = max(lift, 200_000)
    if len(instr) > 2500:
        lift = max(lift, 400_000)
    if sig["should_split"]:
        lift = max(lift, 500_000)
    if sig["health_like"]:
        lift = max(lift, kind_budget_floor("health_check") or 180_000)
    # dogfood / 马拉松 / 多阶段关键词
    if re.search(r"(马拉松|≥\s*2\s*小时|>=\s*2\s*h|多阶段|至少\s*\d+\s*阶段|dogfood)", instr, re.I):
        lift = max(lift, 800_000)

    out = max(floor, lift) if lift else floor
    if out <= 0:
        return fallback
    return min(out, cap)


def budget_for_identity(
    ident: Any,
    instruction: str = "",
) -> int | None:
    base = getattr(ident, "default_token_budget", None)
    return suggested_token_budget(
        base=base if base is None else int(base),
        instruction=instruction or "",
        role=getattr(ident, "role", None),
        name=getattr(ident, "name", None),
    )


def resolve_interactive_chat_budget(
    *,
    user_input: str = "",
    is_steward: bool = False,
    explicit: int | None = None,
    history_tokens_est: int = 0,
) -> int | None:
    """Main-chat / CEO session process token_budget (auto-allocate).

    Workforce jobs use ``resolve_job_budget``; interactive CEO chat used to
    inherit coding profile 200k only — long history burns it in 3–5 LLM
    rounds (~50–90k billable each). Steward/CEO gets a higher floor and
    scales with instruction + rough history size.
    """
    if explicit is not None:
        try:
            return clamp_ceo_budget(int(explicit))
        except Exception:
            return explicit
    # Floors: CEO/steward orchestration needs room for multi-tool deep work.
    # Marathon evidence: 400k dies in ~8–12 LLM rounds under long context.
    base = 1_500_000 if is_steward else 800_000
    # Long context sessions: each turn re-sends history
    if history_tokens_est >= 40_000:
        base = max(base, 2_000_000 if is_steward else 1_200_000)
    if history_tokens_est >= 80_000:
        base = max(base, 2_500_000 if is_steward else 1_500_000)
    if history_tokens_est >= 150_000:
        base = max(base, 3_000_000 if is_steward else 2_000_000)
    return suggested_token_budget(
        base=base,
        instruction=user_input or "",
        role="CEO" if is_steward else None,
        name="steward" if is_steward else None,
    )


def hard_cap() -> int:
    return _settings_hard_cap()


def clamp_ceo_budget(value: int) -> int:
    """CEO 显式预算：0=不限；>0 夹到 [1000, hard_cap]。

    hard_cap 默认 200 万（原 50 万导致 90 万 payload 被夹死）。
    """
    if value == 0:
        return 0
    if value < 0:
        raise ValueError("token_budget 不能为负（0=不限，正整数=硬顶）")
    return max(1_000, min(int(value), hard_cap()))


def parse_payload_token_budget(payload: Any) -> int | None:
    """从工单 payload 读 CEO 指定预算。None=未指定。"""
    if not isinstance(payload, dict):
        return None
    if "token_budget" not in payload and "budget" not in payload:
        return None
    raw = payload.get("token_budget", payload.get("budget"))
    if raw is None or raw == "":
        return None
    try:
        return clamp_ceo_budget(int(raw))
    except (TypeError, ValueError):
        return None


def resolve_job_budget(
    ident: Any,
    instruction: str = "",
    *,
    payload: Any = None,
    ceo_token_budget: int | None = None,
) -> tuple[int | None, str]:
    """工单有效预算 + 来源说明。

    CEO 显式 token_budget 语义（产品红线）：
    - 0 = 本单不限
    - 正整数默认 **floor（只抬高）**：max(CEO 指定, 档案+任务类自动抬升)
      避免 CEO 随手写 80k 把 audit/health 的 150k+ 地板踩扁
    - payload.budget_mode=absolute / budget_hard=true → 绝对硬顶（可低于 auto）

    返回 (budget, source) 如 ceo_floor / ceo_absolute / auto。
    """
    explicit = ceo_token_budget
    if explicit is None:
        explicit = parse_payload_token_budget(payload)
    auto = budget_for_identity(ident, instruction or "")

    if explicit is None:
        return auto, "auto"

    if explicit == 0:
        return 0, "ceo_unlimited"

    # Absolute hard cap only when CEO explicitly opts in
    mode = ""
    hard = False
    if isinstance(payload, dict):
        mode = str(
            payload.get("budget_mode") or payload.get("token_budget_mode") or ""
        ).strip().lower()
        hard = payload.get("budget_hard") in (True, "true", "1", 1, "yes")
        hard = hard or payload.get("token_budget_hard") in (True, "true", "1", 1, "yes")
    if mode in ("absolute", "hard", "cap", "set") or hard:
        return explicit, "ceo_absolute"

    # Default floor: never let CEO override *lower* than auto lift
    if auto is not None and auto > 0 and explicit > 0:
        if auto > explicit:
            return auto, "ceo_floor+auto"
        return explicit, "ceo_floor"
    return explicit, "ceo_floor"
