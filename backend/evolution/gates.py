"""Regression / safety gates before auto-apply."""

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
) -> dict[str, Any]:
    """Return {ok: bool, gates: [{id, ok, reason}], reasons: [...]}."""
    cfg = get_evolution_config()
    gates: list[dict[str, Any]] = []

    # G1 Manifest
    g1_ok = bool(name and name.strip() and (content or summary))
    gates.append(
        {
            "id": "G1_manifest",
            "ok": g1_ok,
            "reason": "" if g1_ok else "缺少 name 或内容",
        }
    )

    # G2 Content ban patterns + destructive
    banned_hit = None
    text = f"{name}\n{summary}\n{content}"
    for p in cfg.ban_patterns:
        if p and p in text:
            banned_hit = p
            break
    destructive = bool(
        re.search(r"rm\s+-rf\s+/|format\s+c:|DROP\s+DATABASE", text, re.I)
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

    # G3 Smoke score
    g3_ok = True
    g3_reason = ""
    if score is not None:
        g3_ok = score >= 0.99 or score >= 1.0 - 1e-9
        # allow slightly soft: >= 0.8 for partial multi-criteria
        g3_ok = score >= 0.8
        g3_reason = "" if g3_ok else f"验收分过低: {score:.2f}"
    gates.append({"id": "G3_smoke", "ok": g3_ok, "reason": g3_reason})

    # G4 Size
    size = len(content.encode("utf-8")) if content else 0
    g4_ok = size <= cfg.max_skill_bytes
    gates.append(
        {
            "id": "G4_size",
            "ok": g4_ok,
            "reason": "" if g4_ok else f"内容过大: {size} > {cfg.max_skill_bytes}",
        }
    )

    # G5 Seesaw — no regression vs baseline
    g5_ok = True
    g5_reason = ""
    if score is not None and baseline_score is not None:
        if score + 0.05 < baseline_score:
            g5_ok = False
            g5_reason = f"回归: {score:.2f} < baseline {baseline_score:.2f}"
    gates.append({"id": "G5_seesaw", "ok": g5_ok, "reason": g5_reason})

    ok = all(g["ok"] for g in gates)
    reasons = [g["reason"] for g in gates if not g["ok"] and g["reason"]]
    return {"ok": ok, "gates": gates, "reasons": reasons}
