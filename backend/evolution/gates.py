"""Regression / safety gates before auto-apply (v0.1.1 + structure)."""

from __future__ import annotations

import re
from typing import Any

from backend.evolution.config import get_evolution_config


def run_gates(
    *,
    name: str,
    content: str,
    summary: str = "",
    score: float | None = None,
    baseline_score: float | None = None,
    kind: str = "skill",
) -> dict[str, Any]:
    """Return {ok, gates, reasons}."""
    cfg = get_evolution_config()
    gates: list[dict[str, Any]] = []

    g1_ok = bool(name and name.strip() and (content or summary))
    gates.append(
        {
            "id": "G1_manifest",
            "ok": g1_ok,
            "reason": "" if g1_ok else "缺少 name 或内容",
        }
    )

    banned_hit = None
    text = f"{name}\n{summary}\n{content}"
    for p in cfg.ban_patterns:
        if p and p in text:
            banned_hit = p
            break
    destructive = bool(
        re.search(r"rm\s+-rf\s+/|format\s+c:|DROP\s+DATABASE|os\.system\(", text, re.I)
    )
    g2_ok = banned_hit is None and not destructive
    gates.append(
        {
            "id": "G2_content",
            "ok": g2_ok,
            "reason": (
                ""
                if g2_ok
                else (f"命中敏感模式: {banned_hit}" if banned_hit else "疑似破坏性内容")
            ),
        }
    )

    g3_ok = True
    g3_reason = ""
    if score is not None:
        g3_ok = score >= 0.8
        g3_reason = "" if g3_ok else f"验收分过低: {score:.2f}"
    gates.append({"id": "G3_smoke", "ok": g3_ok, "reason": g3_reason})

    size = len(content.encode("utf-8")) if content else 0
    g4_ok = size <= cfg.max_skill_bytes
    gates.append(
        {
            "id": "G4_size",
            "ok": g4_ok,
            "reason": "" if g4_ok else f"内容过大: {size} > {cfg.max_skill_bytes}",
        }
    )

    g5_ok = True
    g5_reason = ""
    if score is not None and baseline_score is not None:
        if score + 0.05 < baseline_score:
            g5_ok = False
            g5_reason = f"回归: {score:.2f} < baseline {baseline_score:.2f}"
    gates.append({"id": "G5_seesaw", "ok": g5_ok, "reason": g5_reason})

    # G6 structure (P2): skill should declare when-to-use
    g6_ok = True
    g6_reason = ""
    if kind in {"skill", "tool"} and content:
        has_when = bool(
            re.search(r"when to use|适用|何时", content, re.I)
            or content.strip().startswith("---")
        )
        has_steps = bool(re.search(r"##\s*steps|##\s*步骤|推荐步骤", content, re.I))
        g6_ok = has_when and (has_steps or len(content) > 400)
        g6_reason = "" if g6_ok else "缺少 When to use / 步骤结构"
    gates.append({"id": "G6_structure", "ok": g6_ok, "reason": g6_reason})

    # G7 tools must not ship raw executable python
    g7_ok = True
    g7_reason = ""
    if kind == "tool" and content:
        if re.search(r"subprocess\.|os\.system|eval\(|exec\(", content):
            g7_ok = False
            g7_reason = "tool 草稿禁止可执行危险代码"
    gates.append({"id": "G7_tool_safe", "ok": g7_ok, "reason": g7_reason})

    ok = all(g["ok"] for g in gates)
    reasons = [g["reason"] for g in gates if not g["ok"] and g["reason"]]
    return {"ok": ok, "gates": gates, "reasons": reasons}
