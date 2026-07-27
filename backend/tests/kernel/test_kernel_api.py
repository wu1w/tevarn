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
    finally:
        asyncio.run(kernel.end_process(proc.id, state="completed"))


def test_processes_excludes_terminal_by_default() -> None:
    kernel = get_kernel()

    async def go():
        p = await kernel.create_process("main")
        await kernel.end_process(p.id, state="completed")

    asyncio.run(go())
    r = _client().get("/api/kernel/processes")
    assert r.json()["total"] == 0
    r2 = _client().get("/api/kernel/processes?include_terminal=true")
    assert r2.json()["total"] == 1


def test_events_endpoint_filters_by_kind() -> None:
    kernel = get_kernel()

    async def go():
        p = await kernel.create_process("main", capabilities=["file_read"])
        await kernel.mediate(p.id, "tool_call", "file_read")

    asyncio.run(go())
    r = _client().get("/api/kernel/events?kind=mediation")
    assert r.status_code == 200
    events = r.json()["events"]
    assert len(events) == 1
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

    asyncio.run(go())
    r = _client().get("/api/kernel/events?kind=mediation")
    events = r.json()["events"]
    assert len(events) == 1
    assert events[0]["detail"]["allowed"] is False
    assert "terminal" in events[0]["detail"]["target"]
