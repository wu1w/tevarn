"""bench 结果汇总 + 跨 sha 对比（T6）。

单次跑分只能告诉你「现在多少分」；真正有用的是**两次之间的差**——
「我改了 prompt 之后是变强还是变弱」正是此前无法回答的问题。
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def summarize(runs: list[dict[str, Any]]) -> dict[str, Any]:
    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in runs:
        by_task[r["task"]].append(r)

    tasks: dict[str, Any] = {}
    for name, rs in sorted(by_task.items()):
        passed = sum(1 for r in rs if r["passed"])
        tasks[name] = {
            "runs": len(rs),
            "passed": passed,
            "pass_rate": passed / len(rs) if rs else 0.0,
            "avg_iterations": _avg(rs, "iterations"),
            "avg_tool_calls": _avg(rs, "tool_calls"),
            "avg_seconds": _avg(rs, "wall_seconds"),
            "avg_prompt_tokens": _avg(rs, "prompt_tokens"),
            "avg_cache_read_tokens": _avg(rs, "cache_read_tokens"),
            # 失败时最有用的信息是「哪条断言没过」，直接留在摘要里
            "failed_assertions": sorted(
                {
                    f"{a['type']}: {a['detail']}"
                    for r in rs
                    if not r["passed"]
                    for a in r.get("assertions", [])
                    if not a["ok"]
                }
            )[:5],
            "errors": sorted({r["error"] for r in rs if r.get("error")})[:3],
        }

    total = len(runs)
    total_passed = sum(1 for r in runs if r["passed"])
    prompt_total = sum(int(r.get("prompt_tokens") or 0) for r in runs)
    cache_total = sum(int(r.get("cache_read_tokens") or 0) for r in runs)
    return {
        "total_runs": total,
        "passed": total_passed,
        "pass_rate": total_passed / total if total else 0.0,
        "avg_iterations": _avg(runs, "iterations"),
        "avg_tool_calls": _avg(runs, "tool_calls"),
        "avg_seconds": _avg(runs, "wall_seconds"),
        "total_prompt_tokens": prompt_total,
        "total_cache_read_tokens": cache_total,
        "cache_hit_rate": (cache_total / prompt_total) if prompt_total else 0.0,
        "tasks": tasks,
    }


def _avg(rs: list[dict[str, Any]], key: str) -> float:
    vals = [float(r.get(key) or 0) for r in rs]
    return round(sum(vals) / len(vals), 2) if vals else 0.0


def render_markdown(summary: dict[str, Any], meta: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"# Takton Agent Bench — {meta.get('label', 'run')}")
    lines.append("")
    lines.append(
        f"- 提交: `{meta.get('git_sha', 'unknown')}`　模型: `{meta.get('model', '?')}`　"
        f"重复: {meta.get('repeat', 1)}"
    )
    lines.append(f"- 时间: {meta.get('started_at', '')}")
    lines.append("")
    lines.append(f"## 通过率 {summary['pass_rate']:.0%} "
                 f"({summary['passed']}/{summary['total_runs']})")
    lines.append("")
    lines.append(
        f"平均轮数 {summary['avg_iterations']}　平均工具调用 {summary['avg_tool_calls']}　"
        f"平均耗时 {summary['avg_seconds']}s"
    )
    lines.append(
        f"输入 token {summary['total_prompt_tokens']}　"
        f"缓存命中 {summary['cache_hit_rate']:.0%}"
    )
    lines.append("")
    lines.append("| 任务 | 通过 | 轮数 | 工具 | 秒 | 首个失败断言 |")
    lines.append("|---|---|---|---|---|---|")
    for name, t in summary["tasks"].items():
        mark = "✅" if t["pass_rate"] == 1 else ("⚠️" if t["passed"] else "❌")
        why = (t["failed_assertions"] or t["errors"] or [""])[0]
        lines.append(
            f"| {name} | {mark} {t['passed']}/{t['runs']} | {t['avg_iterations']} | "
            f"{t['avg_tool_calls']} | {t['avg_seconds']} | {why[:80]} |"
        )
    return "\n".join(lines) + "\n"


def compare(base: dict[str, Any], head: dict[str, Any]) -> str:
    """两次结果的差异表 —— bench 的核心用途。"""
    lines = ["# Bench 对比", ""]
    d = head["pass_rate"] - base["pass_rate"]
    arrow = "↑" if d > 0 else ("↓" if d < 0 else "→")
    lines.append(
        f"**通过率 {base['pass_rate']:.0%} {arrow} {head['pass_rate']:.0%}** "
        f"({d:+.0%})"
    )
    lines.append("")
    for key, label, lower_better in [
        ("avg_iterations", "平均轮数", True),
        ("avg_tool_calls", "平均工具调用", True),
        ("avg_seconds", "平均耗时(s)", True),
        ("total_prompt_tokens", "输入 token 总量", True),
        ("cache_hit_rate", "缓存命中率", False),
    ]:
        b, h = base.get(key, 0), head.get(key, 0)
        delta = h - b
        good = (delta < 0) if lower_better else (delta > 0)
        tag = "✅" if delta and good else ("⚠️" if delta else "")
        fmt = (lambda v: f"{v:.0%}") if key == "cache_hit_rate" else (lambda v: f"{v:g}")
        lines.append(f"- {label}: {fmt(b)} → {fmt(h)} ({delta:+g}) {tag}")
    lines.append("")

    lines.append("## 任务级变化")
    regressed, fixed = [], []
    for name in sorted(set(base["tasks"]) | set(head["tasks"])):
        b = base["tasks"].get(name, {}).get("pass_rate", 0.0)
        h = head["tasks"].get(name, {}).get("pass_rate", 0.0)
        if h < b:
            regressed.append(f"- ❌ {name}: {b:.0%} → {h:.0%}")
        elif h > b:
            fixed.append(f"- ✅ {name}: {b:.0%} → {h:.0%}")
    if regressed:
        lines.append("### 变差（需要解释才能合入）")
        lines.extend(regressed)
    if fixed:
        lines.append("### 变好")
        lines.extend(fixed)
    if not regressed and not fixed:
        lines.append("（任务级通过率无变化）")
    return "\n".join(lines) + "\n"


def load_summary(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["summary"] if "summary" in data else data
