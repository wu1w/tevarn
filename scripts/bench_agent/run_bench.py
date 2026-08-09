#!/usr/bin/env python3
"""Tevarn Agent Bench —— 回答「这次改动让 agent 变强还是变弱」（T6）。

此前 docs/CORE_RUNTIME.md 引用了本文件但它并不存在；73 个单测全是结构性的
（freeze / 契约 / 单元），没有一个能度量 agent 的实际能力。
没有 bench，对 system_prompt 的每次修改都只是凭感觉。

用法：
    # 跑全部任务，重复 3 次
    .venv/bin/python -m scripts.bench_agent.run_bench --repeat 3

    # 只跑某几个任务
    .venv/bin/python -m scripts.bench_agent.run_bench --tasks fix_bug_01,read_only_01

    # 与上一次结果对比（bench 的主要用途）
    .venv/bin/python -m scripts.bench_agent.run_bench --compare bench/results/<旧>.json

    # 冒烟：不调用 LLM，只验证 harness 自身通路
    .venv/bin/python -m scripts.bench_agent.run_bench --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.bench_agent.report import (  # noqa: E402
    compare,
    load_summary,
    render_markdown,
    summarize,
)
from scripts.bench_agent.runner import prepare_workspace, run_task  # noqa: E402

TASKS_DIR = Path(__file__).parent / "tasks"
FIXTURES_DIR = Path(__file__).parent / "fixtures"
RESULTS_DIR = _ROOT / "bench" / "results"


def load_tasks(only: list[str] | None) -> list[dict[str, Any]]:
    import yaml

    tasks: list[dict[str, Any]] = []
    for p in sorted(TASKS_DIR.glob("*.yaml")):
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not data.get("name"):
            raise ValueError(f"任务文件格式错误（缺 name）: {p}")
        if only and data["name"] not in only:
            continue
        data["_path"] = str(p)
        tasks.append(data)
    if only:
        missing = set(only) - {t["name"] for t in tasks}
        if missing:
            raise SystemExit(f"未找到任务: {', '.join(sorted(missing))}")
    return tasks


def git_sha() -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(_ROOT), capture_output=True, text=True, timeout=10,
        )
        return (r.stdout or "").strip() or "nogit"
    except Exception:
        return "nogit"


def check_llm_ready() -> str:
    """没有可用 LLM 就直接拒绝跑，绝不产出看似成功的假数据。"""
    from backend.core.config import settings

    cfg = settings.get_llm_config()
    model = getattr(cfg, "model", "") or ""
    base = getattr(cfg, "base_url", "") or ""
    key = getattr(cfg, "api_key", "") or ""
    if not model or not base:
        raise SystemExit(
            "未配置 LLM（llm_model / llm_base_url 为空）。\n"
            "bench 必须打真实模型才有意义；请先配置，或用 --dry-run 只验证 harness。"
        )
    if not key and "localhost" not in base and "127.0.0.1" not in base:
        print(f"[warn] 远端 base_url={base} 但未配置 api_key，可能会 401", file=sys.stderr)
    return model


async def _bootstrap_runtime() -> None:
    """把 DB 里的 LLM 目录/密钥/工具加载进本进程（不依赖 uvicorn lifespan）。

    对 ChatGPT OAuth / xAI OAuth / 任意 catalog 供应商一视同仁：
    环境变量常为空，真实配置在 SQLite settings 表。
    """
    try:
        from backend.core.runtime_settings import load_settings_from_db

        applied = await load_settings_from_db()
        print(f"[bench] loaded {len(applied)} runtime settings from DB", flush=True)
    except Exception as e:
        print(f"[bench] load_settings_from_db skipped: {e}", flush=True)
    try:
        from backend.tools.loader import load_all_tools
        from backend.tools.registry import ToolRegistry

        if not ToolRegistry.get_all():
            await load_all_tools()
        print(f"[bench] ToolRegistry n={len(ToolRegistry.get_all())}", flush=True)
    except Exception as e:
        print(f"[bench] load_all_tools skipped: {e}", flush=True)


async def main_async(args: argparse.Namespace) -> int:
    tasks = load_tasks(args.tasks.split(",") if args.tasks else None)
    if not tasks:
        raise SystemExit("没有任务可跑")

    if not args.dry_run:
        await _bootstrap_runtime()
    model = "dry-run" if args.dry_run else check_llm_ready()

    runs: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="tevarn-bench-") as tmp:
        tmp_root = Path(tmp)
        for task in tasks:
            for i in range(args.repeat):
                ws = tmp_root / f"{task['name']}-{i}"
                fixture = (
                    FIXTURES_DIR / task["fixture"] if task.get("fixture") else None
                )
                prepare_workspace(fixture, ws)

                if args.dry_run:
                    # 只验证 fixture / 断言通路，不碰 LLM
                    from scripts.bench_agent.assertions import run_assertions
                    from scripts.bench_agent.runner import TaskRun, snapshot_workspace

                    base = snapshot_workspace(ws)
                    specs = []
                    for s in task.get("assertions", []):
                        s = dict(s)
                        if s.get("type") == "workspace_unchanged":
                            s["_baseline"] = base
                        specs.append(s)
                    r = TaskRun(task=task["name"], repeat_index=i, passed=False)
                    r.assertions = run_assertions(ws, specs, "")
                    r.error = "dry-run（未调用 LLM）"
                    runs.append(r.to_dict())
                    print(f"  [dry] {task['name']}#{i}: 断言通路 OK "
                          f"({len(r.assertions)} 条)")
                    continue

                print(f"  {task['name']}#{i} ...", end="", flush=True)
                r = await run_task(
                    task,
                    workspace=ws,
                    repeat_index=i,
                    working_mode=args.working_mode,
                    execution_mode=args.execution_mode,
                )
                runs.append(r.to_dict())
                print(
                    f" {'PASS' if r.passed else 'FAIL'} "
                    f"({r.iterations}轮 {r.tool_calls}工具 {r.wall_seconds:.0f}s)"
                )

    summary = summarize(runs)
    meta = {
        "git_sha": git_sha(),
        "model": model,
        "repeat": args.repeat,
        "label": args.label or "run",
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "working_mode": args.working_mode,
        "execution_mode": args.execution_mode,
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"{meta['git_sha']}-{model.replace('/', '_')}-{int(datetime.now().timestamp())}"
    out_json = RESULTS_DIR / f"{stem}.json"
    out_json.write_text(
        json.dumps({"meta": meta, "summary": summary, "runs": runs},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    md = render_markdown(summary, meta)
    (RESULTS_DIR / f"{stem}.md").write_text(md, encoding="utf-8")

    print()
    print(md)
    print(f"结果已写入 {out_json}")

    if args.compare:
        base = load_summary(Path(args.compare))
        print()
        print(compare(base, summary))

    return 0 if summary["pass_rate"] == 1.0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Tevarn Agent Bench")
    ap.add_argument("--tasks", default="", help="逗号分隔的任务名，默认全部")
    ap.add_argument("--repeat", type=int, default=1, help="每个任务重复次数（建议 3）")
    ap.add_argument("--label", default="", help="本次运行的标签")
    ap.add_argument("--compare", default="", help="对比的旧结果 json 路径")
    ap.add_argument("--dry-run", action="store_true", help="不调 LLM，只验证 harness")
    ap.add_argument(
        "--working-mode",
        default="autonomous",
        help="bench 无人值守，默认 autonomous（任何 ask 都会挂死）",
    )
    ap.add_argument(
        "--execution-mode",
        default="local",
        help="fixture 在临时目录，默认 local（沙箱会因 cwd 越界拒绝）",
    )
    args = ap.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
