"""Phase 4.1：技能沙箱回放验证。

蒸馏 draft 在 apply/approved 前：
1. 解析 SKILL.md 结构
2. 若有 origin trajectory / 模拟基线，比较工具错误率与步数
3. 将 {pass, metrics, reason} 写入 asset.meta.replay

MVP 策略（可测、无真 LLM 依赖）：
- 结构门：agentskills frontmatter + 必备章节
- 轨迹门：若 meta.tool_trace 存在，统计 error 率；过高则 fail
- 无轨迹时：结构通过即 pass（标注 mode=heuristic）
- agent_evolution_require_replay=false 时跳过强制
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

logger = logging.getLogger(__name__)


def _setting(name: str, default):
    try:
        from backend.core.config import settings

        v = getattr(settings, name, None)
        return default if v is None else type(default)(v)
    except Exception:
        return default


def _parse_skill_structure(content: str) -> dict[str, Any]:
    text = content or ""
    has_fm = text.lstrip().startswith("---")
    name_m = re.search(r"(?m)^name:\s*(\S+)", text)
    desc_m = re.search(r"(?m)^description:\s*(.+)$", text)
    sections = {
        "适用场景": bool(re.search(r"##\s*适用场景", text)),
        "步骤": bool(re.search(r"##\s*步骤", text)),
        "验证": bool(re.search(r"##\s*验证", text)),
    }
    # 英文兼容
    if not sections["步骤"]:
        sections["步骤"] = bool(re.search(r"##\s*Steps?", text, re.I))
    body_len = len(text)
    return {
        "has_frontmatter": has_fm,
        "has_name": bool(name_m),
        "has_description": bool(desc_m),
        "sections": sections,
        "body_chars": body_len,
        "skill_name": (name_m.group(1) if name_m else "")[:64],
    }


def _trace_metrics(tool_trace: list[dict[str, Any]] | None) -> dict[str, Any]:
    steps = list(tool_trace or [])
    n = len(steps)
    errors = 0
    for t in steps:
        res = str(t.get("result") or t.get("error") or "")
        status = str(t.get("status") or "").lower()
        if status in ("error", "failed", "fail"):
            errors += 1
            continue
        if res.startswith("[Error]") or "Traceback" in res or "Exception" in res[:80]:
            errors += 1
    rate = (errors / n) if n else 0.0
    return {
        "tool_calls": n,
        "tool_errors": errors,
        "tool_error_rate": round(rate, 4),
        "completed": n >= 1 and errors < n,
    }


def validate_skill_replay(
    asset: dict[str, Any],
    *,
    tool_trace: list[dict[str, Any]] | None = None,
    baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """同步验证，返回可写入 meta.replay 的 dict。"""
    content = str(asset.get("content") or "")
    meta = dict(asset.get("meta") or {})
    trace = tool_trace
    if trace is None:
        trace = meta.get("tool_trace") or meta.get("origin_trace")
    if not isinstance(trace, list):
        trace = None

    structure = _parse_skill_structure(content)
    metrics = _trace_metrics(trace)
    base = baseline or meta.get("baseline_metrics") or {}
    if not isinstance(base, dict):
        base = {}

    max_err = float(_setting("agent_evolution_replay_max_tool_error_rate", 0.4))
    min_body = int(_setting("agent_evolution_replay_min_body_chars", 120))
    require_sections = bool(_setting("agent_evolution_replay_require_sections", True))

    reasons: list[str] = []
    ok = True

    if not structure["has_frontmatter"] or not structure["has_name"]:
        ok = False
        reasons.append("missing_frontmatter_or_name")
    if structure["body_chars"] < min_body:
        ok = False
        reasons.append(f"body_too_short<{min_body}")
    if require_sections and not structure["sections"].get("步骤"):
        ok = False
        reasons.append("missing_steps_section")

    if metrics["tool_calls"] > 0:
        if metrics["tool_error_rate"] > max_err:
            ok = False
            reasons.append(
                f"tool_error_rate={metrics['tool_error_rate']:.2f}>{max_err}"
            )
        # 与基线比：错误率显著恶化
        base_rate = base.get("tool_error_rate")
        if base_rate is not None:
            try:
                if float(metrics["tool_error_rate"]) > float(base_rate) + 0.2:
                    ok = False
                    reasons.append("worse_than_baseline_error_rate")
            except (TypeError, ValueError):
                pass
        mode = "trajectory"
    else:
        mode = "heuristic"
        reasons.append("no_trajectory_heuristic_pass" if ok else "no_trajectory")

    result = {
        "pass": bool(ok),
        "mode": mode,
        "checked_at": time.time(),
        "structure": structure,
        "metrics": metrics,
        "baseline": base or None,
        "reason": ";".join(reasons) if reasons else "ok",
        "thresholds": {
            "max_tool_error_rate": max_err,
            "min_body_chars": min_body,
        },
    }
    return result


def attach_replay_to_asset(asset_id: str, replay: dict[str, Any]) -> dict[str, Any] | None:
    """合并 meta.replay 并落库。"""
    from backend.evolution import store

    asset = store.get_asset(asset_id)
    if not asset:
        return None
    meta = dict(asset.get("meta") or {})
    meta["replay"] = replay
    return store.patch_asset_meta(asset_id, meta)


def validate_and_attach(asset_id: str, **kwargs: Any) -> dict[str, Any]:
    from backend.evolution import store

    asset = store.get_asset(asset_id)
    if not asset:
        return {"pass": False, "reason": "asset_not_found"}
    replay = validate_skill_replay(asset, **kwargs)
    attach_replay_to_asset(asset_id, replay)
    return replay


def assert_replay_allows_apply(asset: dict[str, Any]) -> dict[str, Any]:
    """apply 门禁。返回 {ok, replay, message}。

    - require_replay=False → 总是 ok（仍可附带已有 replay 信息）
    - 无 replay meta → 即时跑验证
    - replay.pass=False → ok=False
    """
    require = bool(_setting("agent_evolution_require_replay", True))
    meta = dict(asset.get("meta") or {})
    replay = meta.get("replay")
    if not isinstance(replay, dict) or "pass" not in replay:
        replay = validate_skill_replay(asset)
        try:
            aid = asset.get("id")
            if aid:
                attach_replay_to_asset(str(aid), replay)
        except Exception as e:
            logger.debug("attach replay on apply: %s", e)

    if not require:
        return {"ok": True, "replay": replay, "message": "replay_not_required"}

    if replay.get("pass"):
        return {"ok": True, "replay": replay, "message": "replay_pass"}
    return {
        "ok": False,
        "replay": replay,
        "message": f"replay_failed: {replay.get('reason') or 'unknown'}",
    }


__all__ = [
    "validate_skill_replay",
    "attach_replay_to_asset",
    "validate_and_attach",
    "assert_replay_allows_apply",
]
