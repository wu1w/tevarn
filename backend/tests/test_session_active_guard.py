"""会话活跃保护测试（零 mock：直接布置 manager 真实数据结构 + 真实 DB）。

事故背景：前端「空白会话自动清理」在运行中会话的流式消息尚未落库时，
按 DB 内容判定空白并 DELETE，导致用户切页回来报 Session not found。
修复为三层防线，此处逐一验证：
1. GET /sessions/active-ids 暴露 WS 连接中 / agent 运行中的会话
2. DELETE /sessions/{id} 对活跃会话默认 409 拒删
3. DELETE 带 force=true 放行（用户显式删除路径）
"""

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient

from backend.api.websocket import manager


@pytest.fixture
def clean_manager():
    """每个测试前后清理全局 WS manager 状态，避免用例间污染。"""
    snapshot_conn = dict(manager._connections)
    snapshot_agents = dict(manager._agent_tasks)
    manager._connections.clear()
    manager._agent_tasks.clear()
    try:
        yield
    finally:
        manager._connections.clear()
        manager._agent_tasks.clear()
        manager._connections.update(snapshot_conn)
        manager._agent_tasks.update(snapshot_agents)


def _create_session(client: TestClient) -> str:
    resp = client.post("/api/sessions", json={})
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


def test_active_ids_empty(client: TestClient, clean_manager) -> None:
    resp = client.get("/api/sessions/active-ids")
    assert resp.status_code == 200
    assert resp.json() == []


def test_active_ids_includes_connected_session(client: TestClient, clean_manager) -> None:
    sid = uuid.uuid4()
    manager._connections[sid] = None  # 布置：仅 keys 参与判定，值为占位
    try:
        resp = client.get("/api/sessions/active-ids")
        assert resp.status_code == 200
        assert str(sid) in resp.json()
    finally:
        manager._connections.pop(sid, None)


async def test_active_ids_includes_running_agent(client: TestClient, clean_manager) -> None:
    sid = uuid.uuid4()
    task = asyncio.create_task(asyncio.sleep(30))
    manager.track_agent_task(sid, task)
    try:
        resp = client.get("/api/sessions/active-ids")
        assert resp.status_code == 200
        assert str(sid) in resp.json()
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    # 任务结束后不再算活跃
    resp = client.get("/api/sessions/active-ids")
    assert str(sid) not in resp.json()


def test_delete_active_session_rejected_409(client: TestClient, clean_manager) -> None:
    sid = _create_session(client)
    manager._connections[uuid.UUID(sid)] = None
    try:
        resp = client.delete(f"/api/sessions/{sid}")
        assert resp.status_code == 409
        assert "active" in resp.json()["detail"].lower()
        # 会话仍在，未被误删
        assert client.get(f"/api/sessions/{sid}").status_code == 200
    finally:
        manager._connections.pop(uuid.UUID(sid), None)


async def test_delete_running_agent_session_rejected_409(
    client: TestClient, clean_manager
) -> None:
    sid = _create_session(client)
    task = asyncio.create_task(asyncio.sleep(30))
    manager.track_agent_task(uuid.UUID(sid), task)
    try:
        resp = client.delete(f"/api/sessions/{sid}")
        assert resp.status_code == 409
        assert client.get(f"/api/sessions/{sid}").status_code == 200
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def test_delete_active_session_force_allowed(client: TestClient, clean_manager) -> None:
    sid = _create_session(client)
    manager._connections[uuid.UUID(sid)] = None
    try:
        resp = client.delete(f"/api/sessions/{sid}?force=true")
        assert resp.status_code == 200
        assert client.get(f"/api/sessions/{sid}").status_code == 404
    finally:
        manager._connections.pop(uuid.UUID(sid), None)


def test_delete_inactive_session_still_works(client: TestClient, clean_manager) -> None:
    """回归：非活跃会话的普通删除不受活跃保护影响。"""
    sid = _create_session(client)
    resp = client.delete(f"/api/sessions/{sid}")
    assert resp.status_code == 200
    assert client.get(f"/api/sessions/{sid}").status_code == 404
