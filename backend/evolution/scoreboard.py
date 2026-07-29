"""Skill Scoreboard —— 技能效果计分 + 退化自动回滚（P1b，2026-07-29 夜间路线图）。

核心主张（与 Hermes learning loop 的差异化）：**受治理的自进化**。
- 每次进化技能被使用后记 outcome（成功/失败/token 成本）
- 新一代（gen N）上线后与上一代（gen N-1）滚动窗口对比：
  样本足够且成功率显著退化 → 自动回滚到上一代，并写 kernel 审计事件
- 回滚是降级而非删除：gen N 标记 archived，gen N-1 恢复 applied，
  证据链（outcome 记录）保留，人可在审批面板复盘

阈值全部走 settings（agent_evolution_* 前缀，与 KERNEL_PLAN #3 的参数化原则一致）。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 兜底默认值（settings 缺失时）
DEFAULT_MIN_SAMPLES = 8          # 两代都至少这么多样本才比较
DEFAULT_REGRESSION_DELTA = 0.15  # 成功率跌幅超过 15 个百分点判退化
DEFAULT_WINDOW = 50              # 滚动窗口


def _setting(name: str, default):
    try:
        from backend.core.config import settings

        v = getattr(settings, name, None)
        return default if v is None else type(default)(v)
    except Exception:
        return default


def record_outcome(
    *,
    skill_name: str,
    success: bool,
    tokens: int | None = None,
    session_id: str | None = None,
) -> None:
    """记录一次进化技能使用结局。吞异常（RunRecorder 契约）。"""
    try:
        from backend.evolution import store

        asset = store.latest_asset_by_name("skill", skill_name)
        if asset is None:
            return  # 非进化技能不计分
        store.add_skill_outcome(
            skill_name=skill_name,
            gen=int(asset.get("gen") or 0),
            success=success,
            tokens=tokens,
            session_id=session_id,
        )
    except Exception as e:
        logger.warning("scoreboard.record_outcome swallowed: %s", e)


def check_regression(skill_name: str) -> dict[str, Any]:
    """比较当前代与上一代。返回诊断 dict（供 API/测试），不执行回滚。"""
    from backend.evolution import store

    asset = store.latest_asset_by_name("skill", skill_name)
    if asset is None:
        return {"skill": skill_name, "verdict": "not_found"}
    gen = int(asset.get("gen") or 0)
    if gen < 1:
        return {"skill": skill_name, "verdict": "no_previous_gen", "gen": gen}
    prev = store.asset_by_name_gen("skill", skill_name, gen - 1)
    if prev is None:
        return {"skill": skill_name, "verdict": "no_previous_gen", "gen": gen}

    window = _setting("agent_evolution_score_window", DEFAULT_WINDOW)
    min_samples = _setting("agent_evolution_score_min_samples", DEFAULT_MIN_SAMPLES)
    delta = _setting("agent_evolution_regression_delta", DEFAULT_REGRESSION_DELTA)

    cur = store.skill_outcome_stats(skill_name, gen, window=window)
    old = store.skill_outcome_stats(skill_name, gen - 1, window=window)

    result = {
        "skill": skill_name,
        "gen": gen,
        "current": cur,
        "previous": old,
        "min_samples": min_samples,
        "regression_delta": delta,
    }
    if cur["samples"] < min_samples or old["samples"] < min_samples:
        result["verdict"] = "insufficient_samples"
        return result
    drop = (old["success_rate"] or 0.0) - (cur["success_rate"] or 0.0)
    result["drop"] = round(drop, 4)
    result["verdict"] = "regressed" if drop > delta else "healthy"
    return result


async def maybe_rollback(skill_name: str, *, kernel_process_id: str | None = None) -> dict[str, Any]:
    """诊断 + 退化时执行回滚。回滚动作写 kernel 审计事件（哈希链留痕）。"""
    diag = check_regression(skill_name)
    if diag.get("verdict") != "regressed":
        return diag

    try:
        from backend.evolution import store
        from backend.evolution.skill_sync import upsert_skill_from_asset

        gen = diag["gen"]
        cur_asset = store.asset_by_name_gen("skill", skill_name, gen)
        prev_asset = store.asset_by_name_gen("skill", skill_name, gen - 1)
        if cur_asset is None or prev_asset is None:
            diag["rollback"] = {"ok": False, "reason": "asset_missing"}
            return diag

        # 降级不删除：证据保留
        store.update_asset_status(cur_asset["id"], "archived")
        store.update_asset_status(prev_asset["id"], "applied")
        sync = await upsert_skill_from_asset(
            name=skill_name,
            summary=prev_asset.get("summary") or skill_name,
            content=prev_asset.get("content") or "",
            asset_id=prev_asset["id"],
            kind="skill",
            enabled=True,
        )
        diag["rollback"] = {"ok": True, "restored_gen": gen - 1, "sync": sync}
        logger.warning(
            "skill auto-rollback: %s gen %s -> %s (drop=%.2f)",
            skill_name, gen, gen - 1, diag.get("drop") or 0.0,
        )

        # kernel 审计留痕（best-effort）
        try:
            from backend.kernel import get_kernel

            k = get_kernel()
            pid = kernel_process_id or "system"
            k._emit(  # noqa: SLF001 —— 复用内核事件链；若后续开公共 emit API 再切换
                "evolution_rollback",
                pid,
                {
                    "skill": skill_name,
                    "from_gen": gen,
                    "to_gen": gen - 1,
                    "drop": diag.get("drop"),
                    "current": diag["current"],
                    "previous": diag["previous"],
                },
            )
        except Exception as e:
            logger.debug("scoreboard kernel emit skipped: %s", e)
    except Exception as e:
        logger.warning("scoreboard.maybe_rollback failed: %s", e)
        diag["rollback"] = {"ok": False, "reason": str(e)}
    return diag
