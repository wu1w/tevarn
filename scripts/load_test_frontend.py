#!/usr/bin/env python3
"""Takton 全链路压测——从前端（:3001 Next rewrites 代理）发起

覆盖：页面 HTML + 全部核心 API（读）+ goals CRUD 写循环。
阶段：1 → 10 → 50 → 100 并发梯度。

用法：
    .venv311/bin/python scripts/load_test_frontend.py [--base http://127.0.0.1:3001]

产出：scripts/load_test/results/<ts>/report.json + report.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import httpx

# ── 场景定义 ──
# (name, method, path, weight) 权重≈真实页面加载时各调用的相对频率
READ_SCENARIOS = [
    ("page.dashboard", "GET", "/", 1),
    ("page.agents", "GET", "/agents", 1),
    ("page.goals", "GET", "/goals", 1),
    ("page.activity", "GET", "/activity", 1),
    ("page.kernel", "GET", "/kernel", 1),
    ("page.approvals", "GET", "/approvals", 1),
    ("page.knowledge", "GET", "/knowledge", 1),
    ("api.health", "GET", "/api/health", 2),
    ("api.identities", "GET", "/api/kernel/identities", 3),
    ("api.processes", "GET", "/api/kernel/processes", 3),
    ("api.events", "GET", "/api/kernel/events?limit=200", 2),
    ("api.escalations", "GET", "/api/kernel/escalations?status=pending", 3),
    ("api.goal_tree", "GET", "/api/goals/tree", 3),
    ("api.documents", "GET", "/api/knowledge/documents", 1),
    ("api.notifications", "GET", "/api/notifications", 1),
]

STAGES = [(1, 6), (10, 12), (50, 12), (100, 12)]  # (并发, 秒)
LT_PREFIX = "[LT]"


@dataclass
class Bucket:
    lat: list[float] = field(default_factory=list)
    errors: int = 0
    count: int = 0

    def add(self, ms: float, ok: bool):
        self.count += 1
        if not ok:
            self.errors += 1
        self.lat.append(ms)

    def summary(self) -> dict:
        if not self.lat:
            return {"count": 0}
        s = sorted(self.lat)
        n = len(s)
        return {
            "count": self.count,
            "errors": self.errors,
            "error_rate": round(self.errors / max(1, self.count), 4),
            "p50": round(s[int(n * 0.5)], 1),
            "p95": round(s[min(n - 1, int(n * 0.95))], 1),
            "p99": round(s[min(n - 1, int(n * 0.99))], 1),
            "max": round(s[-1], 1),
            "mean": round(statistics.mean(s), 1),
        }


async def worker(client: httpx.AsyncClient, pool: list, start_idx: int, deadline: float, buckets: dict[str, Bucket]):
    idx = start_idx
    while time.monotonic() < deadline:
        name, method, path, _w = pool[idx % len(pool)]
        idx += 1
        t0 = time.monotonic()
        ok = True
        try:
            r = await client.request(method, path, timeout=10.0)
            if r.status_code >= 500:
                ok = False
        except Exception:
            ok = False
        buckets[name].add((time.monotonic() - t0) * 1000, ok)


async def crud_loop(client: httpx.AsyncClient, deadline: float, bucket: Bucket):
    """goals 完整 CRUD 写循环（低并发 1 个协程跑）。"""
    while time.monotonic() < deadline:
        t0 = time.monotonic()
        ok = True
        gid = None
        try:
            r = await client.post("/api/goals", json={
                "title": f"{LT_PREFIX} 压测目标 {int(t0)}",
                "kind": "objective",
            }, timeout=10.0)
            if r.status_code >= 400:
                ok = False
            else:
                gid = r.json().get("id")
            if gid:
                r2 = await client.put(f"/api/goals/{gid}", json={"progress": 50}, timeout=10.0)
                if r2.status_code >= 400:
                    ok = False
                r3 = await client.delete(f"/api/goals/{gid}", timeout=10.0)
                if r3.status_code >= 400:
                    ok = False
        except Exception:
            ok = False
        bucket.add((time.monotonic() - t0) * 1000, ok)
        await asyncio.sleep(0.3)


def weighted_pool() -> list:
    pool = []
    for sc in READ_SCENARIOS:
        pool.extend([sc] * sc[3])
    return pool


async def run_stage(base: str, concurrency: int, seconds: int, stage_buckets: dict[str, Bucket]):
    limits = httpx.Limits(max_connections=concurrency * 2, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(base_url=base, limits=limits, follow_redirects=True) as client:
        deadline = time.monotonic() + seconds
        pool = weighted_pool()
        tasks = []
        for i in range(concurrency):
            tasks.append(asyncio.create_task(worker(client, pool, i, deadline, stage_buckets)))
        if concurrency >= 10:  # 10 并发起加一条 CRUD 写循环
            tasks.append(asyncio.create_task(crud_loop(client, deadline, stage_buckets["crud.goals"])))
        await asyncio.gather(*tasks)


async def sample_backend_resources(samples: list, stop: asyncio.Event, backend_pid: int | None):
    """采样后端 8090 进程 CPU%/RSS（/proc）。"""
    if not backend_pid:
        return
    clk = 100  # CLOCKS_PER_SEC 常见值
    page_kb = 4
    prev = None
    while not stop.is_set():
        try:
            stat = Path(f"/proc/{backend_pid}/stat").read_text().split()
            utime, stime = int(stat[13]), int(stat[14])
            rss = int(stat[23]) * page_kb // 1024  # MB
            now = time.monotonic()
            if prev:
                dt = now - prev[0]
                cpu = ((utime + stime) - prev[1]) / clk / dt * 100
                samples.append({"cpu_pct": round(cpu, 1), "rss_mb": rss})
            prev = (now, utime + stime)
        except Exception:
            pass
        await asyncio.sleep(1.0)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:3001")
    args = ap.parse_args()

    # 后端 pid（8090）
    backend_pid = None
    import subprocess
    out = subprocess.run(["ss", "-tlnp"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if ":8090" in line and "pid=" in line:
            backend_pid = int(line.split("pid=")[1].split(",")[0])
            break

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    outdir = Path(__file__).parent / "load_test" / "results" / ts
    outdir.mkdir(parents=True, exist_ok=True)

    all_buckets: dict[str, dict[str, Bucket]] = {}
    resource_samples: list = []
    stop_evt = asyncio.Event()
    sampler = asyncio.create_task(sample_backend_resources(resource_samples, stop_evt, backend_pid))

    print(f"base={args.base} backend_pid={backend_pid}")
    for concurrency, seconds in STAGES:
        stage_buckets: dict[str, Bucket] = {sc[0]: Bucket() for sc in READ_SCENARIOS}
        stage_buckets["crud.goals"] = Bucket()
        t0 = time.monotonic()
        await run_stage(args.base, concurrency, seconds, stage_buckets)
        dur = time.monotonic() - t0
        all_buckets[f"c{concurrency}"] = stage_buckets
        total = sum(b.count for b in stage_buckets.values())
        errs = sum(b.errors for b in stage_buckets.values())
        print(f"stage c={concurrency}: {total} reqs in {dur:.0f}s ({total/dur:.1f} rps), errors={errs}")

    stop_evt.set()
    await sampler

    # 清理 LT 残留（理论上 crud_loop 已删，兜底）
    async with httpx.AsyncClient(base_url=args.base, follow_redirects=True) as client:
        try:
            tree = (await client.get("/api/goals/tree")).json()
            for o in tree.get("objectives", []):
                if o["title"].startswith(LT_PREFIX):
                    await client.delete(f"/api/goals/{o['id']}")
        except Exception:
            pass

    report = {
        "base": args.base,
        "ts": ts,
        "stages": {k: {name: b.summary() for name, b in v.items()} for k, v in all_buckets.items()},
        "backend_resources": {
            "cpu_pct_max": max((s["cpu_pct"] for s in resource_samples), default=None),
            "rss_mb_max": max((s["rss_mb"] for s in resource_samples), default=None),
        },
    }
    (outdir / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))

    # markdown 摘要
    lines = [f"# Takton 全链路压测报告 {ts}", f"base: {args.base}", ""]
    for stage, buckets in report["stages"].items():
        total = sum(b.get("count", 0) for b in buckets.values())
        errs = sum(b.get("errors", 0) for b in buckets.values())
        lines.append(f"## 阶段 {stage}（{total} reqs, {errs} errors, {errs/max(1,total)*100:.2f}%）")
        lines.append("| 场景 | count | err | p50 | p95 | p99 | max | mean |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for name, b in sorted(buckets.items()):
            if b.get("count"):
                lines.append(f"| {name} | {b['count']} | {b['errors']} | {b['p50']} | {b['p95']} | {b['p99']} | {b['max']} | {b['mean']} |")
        lines.append("")
    lines.append(f"## 后端资源\n- CPU max: {report['backend_resources']['cpu_pct_max']}%\n- RSS max: {report['backend_resources']['rss_mb_max']} MB")
    (outdir / "report.md").write_text("\n".join(lines))
    print(f"report: {outdir}/report.md")


if __name__ == "__main__":
    asyncio.run(main())
