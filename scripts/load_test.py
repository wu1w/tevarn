"""Tevarn 后端全量压测（本地，无 LLM 付费调用）

场景矩阵：6 端点 × 并发梯度 [1, 10, 25, 50]（health/tools 加测 100）。
指标：RPS / p50 / p95 / p99 / 错误率 / 最大延迟。

用法：.venv311/bin/python scripts/load_test.py [--base http://127.0.0.1:8015] [--quick]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
import uuid

import httpx

BASE = "http://127.0.0.1:8015"

# ─────────── 场景定义 ───────────

def _cluster_execute_body() -> dict:
    return {
        "task_description": "压测任务：立即失败路径",
        "sub_tasks": [{"name": "探针", "prompt": "ping"}],
        "aggregation_strategy": "synthesize",
    }

SCENARIOS = [
    {"name": "health", "method": "GET", "path": "/api/health"},
    {"name": "tools", "method": "GET", "path": "/api/tools"},
    {"name": "workflows", "method": "GET", "path": "/api/workflows"},
    {"name": "cluster_list", "method": "GET", "path": "/api/cluster/list"},
    {
        "name": "cluster_status_404",
        "method": "GET",
        # 每次随机 task_id：DB 回落未命中 → 404（压 miss 路径）
        "path_factory": lambda: f"/api/cluster/status/{uuid.uuid4().hex}",
        "expect": (404,),
    },
    {
        "name": "cluster_execute",
        "method": "POST",
        "path": "/api/cluster/execute",
        "json_factory": _cluster_execute_body,
        # 全异步管线：落库 + 后台任务 + LLM 快速失败 + finish_run 更新
        # 429 为 M4 Budget 设计内背压（超并发槽立即拒绝，非异常）
        "expect": (200, 429),
        "max_concurrency": 25,
        "requests_per_level": 30,
    },
]

LEVELS = [1, 10, 25, 50]
LIGHT_EXTRA = [100]  # 仅 health/tools


async def run_level(client: httpx.AsyncClient, sc: dict, concurrency: int, total: int) -> dict:
    lat: list[float] = []
    errors = 0
    status_counts: dict[int, int] = {}
    expect = sc.get("expect", (200,))
    sem = asyncio.Semaphore(concurrency)

    async def one():
        nonlocal errors
        async with sem:
            path = sc.get("path") or sc["path_factory"]()
            body = sc["json_factory"]() if "json_factory" in sc else None
            t0 = time.perf_counter()
            try:
                r = await client.request(sc["method"], path, json=body)
                dt = (time.perf_counter() - t0) * 1000
                lat.append(dt)
                status_counts[r.status_code] = status_counts.get(r.status_code, 0) + 1
                if r.status_code not in expect:
                    errors += 1
            except Exception:
                errors += 1

    start = time.perf_counter()
    await asyncio.gather(*(one() for _ in range(total)))
    wall = time.perf_counter() - start

    lat.sort()
    n = len(lat)

    def pct(p: float) -> float:
        return lat[min(n - 1, int(n * p))] if n else 0.0

    return {
        "scenario": sc["name"],
        "concurrency": concurrency,
        "total": total,
        "wall_s": round(wall, 2),
        "rps": round(total / wall, 1) if wall else 0,
        "p50": round(pct(0.50), 1),
        "p95": round(pct(0.95), 1),
        "p99": round(pct(0.99), 1),
        "max": round(lat[-1], 1) if n else 0,
        "errors": errors,
        "status": status_counts,
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--quick", action="store_true", help="每档 60 请求（默认 200）")
    args = ap.parse_args()

    results: list[dict] = []
    async with httpx.AsyncClient(
        base_url=args.base,
        timeout=httpx.Timeout(60.0, connect=5.0),
        limits=httpx.Limits(max_connections=200, max_keepalive_connections=100),
    ) as client:
        # 预热
        for _ in range(20):
            await client.get("/api/health")

        for sc in SCENARIOS:
            levels = list(LEVELS)
            if sc["name"] in ("health", "tools"):
                levels += LIGHT_EXTRA
            if "max_concurrency" in sc:
                levels = [l for l in levels if l <= sc["max_concurrency"]]
            for lv in levels:
                total = sc.get("requests_per_level") or (60 if args.quick else 200)
                r = await run_level(client, sc, lv, total)
                results.append(r)
                print(
                    f"{r['scenario']:>20} c={r['concurrency']:>3} "
                    f"rps={r['rps']:>7} p50={r['p50']:>7}ms p95={r['p95']:>8}ms "
                    f"p99={r['p99']:>8}ms max={r['max']:>8}ms err={r['errors']} {r['status']}"
                )

    out = "/tmp/tevarn_load_results.json"
    with open(out, "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n结果已存 {out}")


if __name__ == "__main__":
    asyncio.run(main())
