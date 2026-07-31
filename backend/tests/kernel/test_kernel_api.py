"""Kernel 观测 API 测试（Security Console 数据源）。"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from backend.kernel import get_kernel, reset_kernel_for_tests
from backend.main import app


def _client() -> TestClient:
    return TestClient(app)


def setup_function() -> None:
    reset_kernel_for_tests()
    # kernel routes 会合并 DB 历史进程/提权档案——每测试清空 kernel 表，
    # 否则断言被同进程内先前测试写入的记录污染。
    asyncio.run(_clean_kernel_tables())


async def _clean_kernel_tables() -> None:
    from sqlalchemy import delete

    from backend.database import AsyncSessionLocal
    from backend.models.agent_identity import (
        KernelEscalationRecord,
        KernelProcessRecord,
    )

    try:
        async with AsyncSessionLocal() as s:
            await s.execute(delete(KernelEscalationRecord))
            await s.execute(delete(KernelProcessRecord))
            await s.commit()
    except Exception:
        pass  # 表尚未创建（首个测试 lifespan 建表前）时忽略


def test_processes_endpoint_lists_active() -> None:
    kernel = get_kernel()
    proc = asyncio.run(kernel.create_process("main", session_id="s1", capabilities=["file_read"]))
    try:
        r = _client().get("/api/kernel/processes")
        assert r.status_code == 200
        data = r.json()
        assert data["enabled"] is True and data["total"] == 1
        p = data["processes"][0]
        assert p["id"] == proc.id
        assert p["capabilities"] == ["file_read"]
        assert p["state"] == "created"
        assert "stalled" in p
        assert "stall_threshold_seconds" in data
    finally:
        asyncio.run(kernel.end_process(proc.id, state="completed"))


def test_processes_stalled_when_no_charge_heartbeat(monkeypatch) -> None:
    """P2.3：running 且 last_charge 过久 → stalled=true（Rust host 真实状态）。"""
    import time as time_mod

    kernel = get_kernel()
    monkeypatch.setattr(
        "backend.core.config.settings.agent_process_stall_seconds",
        30.0,
        raising=False,
    )

    async def go():
        p = await kernel.create_process("main", token_budget=50_000)
        # 必须 mark_running：本地改 p.state/meta 不会写回 Rust host
        await kernel.mark_running(p.id)
        return p

    proc = asyncio.run(go())
    base = time_mod.time()
    # API 侧 now 往前拨，模拟长时间无心跳（host 墙钟不变）
    offset = [600.0]
    monkeypatch.setattr(time_mod, "time", lambda: base + offset[0])
    try:
        r = _client().get("/api/kernel/processes")
        assert r.status_code == 200
        rows = {x["id"]: x for x in r.json()["processes"]}
        assert proc.id in rows
        assert rows[proc.id]["stalled"] is True
        assert rows[proc.id]["idle_seconds"] >= 30

        # 心跳写回 host last_charge_at；把 API 时钟拨回正常 → 解除 stalled
        kernel.charge_tokens(proc.id, 1)
        offset[0] = 0.0
        r2 = _client().get("/api/kernel/processes")
        rows2 = {x["id"]: x for x in r2.json()["processes"]}
        assert rows2[proc.id]["stalled"] is False
    finally:
        offset[0] = 0.0
        asyncio.run(kernel.end_process(proc.id, state="completed"))


def test_charge_tokens_sets_last_charge_at() -> None:
    kernel = get_kernel()

    async def go():
        p = await kernel.create_process("main", token_budget=10_000)
        assert not (p.meta or {}).get("last_charge_at")
        kernel.charge_tokens(p.id, 5)
        # Rust 进程视图是快照，需 get_process 再取 meta
        fresh = kernel.get_process(p.id)
        assert fresh is not None
        assert (fresh.meta or {}).get("last_charge_at")
        await kernel.end_process(p.id, state="completed")

    asyncio.run(go())


def test_processes_excludes_terminal_by_default() -> None:
    kernel = get_kernel()

    async def go():
        p = await kernel.create_process("main")
        pid = p.id
        await kernel.end_process(pid, state="completed")
        return pid

    pid = asyncio.run(go())
    r = _client().get("/api/kernel/processes")
    live_ids = {x["id"] for x in r.json()["processes"]}
    assert pid not in live_ids
    r2 = _client().get("/api/kernel/processes?include_terminal=true")
    term_ids = {x["id"] for x in r2.json()["processes"]}
    assert pid in term_ids


def test_events_endpoint_filters_by_kind() -> None:
    kernel = get_kernel()

    async def go():
        p = await kernel.create_process("main", capabilities=["file_read"])
        await kernel.mediate(p.id, "tool_call", "file_read")
        return p.id

    pid = asyncio.run(go())
    r = _client().get(f"/api/kernel/events?kind=mediation&process_id={pid}")
    assert r.status_code == 200
    events = r.json()["events"]
    assert len(events) >= 1
    assert events[0]["detail"]["target"] == "file_read"
    assert events[0]["hash"]  # 哈希链字段透出


def test_events_denied_visible_for_console() -> None:
    """被拦截的 mediation 必须在事件流可见（Console 红条数据源）。"""
    kernel = get_kernel()

    async def go():
        p = await kernel.create_process("main", capabilities=["file_read"])
        try:
            await kernel.mediate(p.id, "tool_call", "terminal")
        except Exception:
            pass
        return p.id

    pid = asyncio.run(go())
    r = _client().get(f"/api/kernel/events?kind=mediation&process_id={pid}")
    events = r.json()["events"]
    denied = [e for e in events if e.get("detail", {}).get("allowed") is False]
    assert len(denied) >= 1
    assert "terminal" in str(denied[0]["detail"].get("target") or "")