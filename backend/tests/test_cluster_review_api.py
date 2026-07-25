"""cluster 路由：后台执行 + WS 进度 + 复核透出（Phase 3 第二刀）测试。

契约变更：/cluster/execute 与 /cluster/execute-plan 不再同步等待，
立即返回 {task_id, status: running, ws_url}；结果经 /cluster/status/{id}
查询（运行中返回 200+running 而非 404），进度经 WS 广播（含终态事件）。
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.agent.cluster_executor import (
    AggregationStrategy,
    ClusterResult,
    SubTask,
    TaskStatus,
)
from backend.api.routes import cluster as cluster_mod


def _make_result() -> ClusterResult:
    t = SubTask(id="task-0", name="调研", description="", prompt="p")
    t.status = TaskStatus.COMPLETED
    t.result = {"status": "success", "result": "ok", "metadata": {}}
    t.metadata["review"] = {"verdict": "pass", "score": 0.9}
    t.completed_at = datetime.now(timezone.utc)
    r = ClusterResult(task_id="x", status=TaskStatus.COMPLETED, sub_tasks=[t])
    r.aggregated_result = {"synthesized": "综合", "review_notes": [], "rejected": []}
    r.metadata["review"] = {"reviewed": 1, "rejected": 0}
    r.completed_at = datetime.now(timezone.utc)
    return r


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(cluster_mod.router)
    with TestClient(app) as c:
        yield c
    # 测试间隔离：清空注册表
    cluster_mod._active_clusters.clear()
    cluster_mod._running_clusters.clear()


def _poll_until_done(client, task_id: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/cluster/status/{task_id}").json()
        if body["status"] != "running":
            return body
        time.sleep(0.05)
    raise AssertionError(f"cluster {task_id} 未在 {timeout}s 内完成")


def test_execute_returns_handle_then_completes(client):
    """execute 立即返回 running 句柄；后台完成后 status 透出 review 汇总"""
    executor = cluster_mod.get_cluster_executor()
    with patch.object(
        executor, "execute", new=AsyncMock(return_value=_make_result())
    ):
        resp = client.post("/cluster/execute", json={
            "task_description": "测试任务",
            "sub_tasks": [{"name": "调研", "prompt": "p"}],
        })
        assert resp.status_code == 200, resp.text
        handle = resp.json()
        assert handle["status"] == "running"
        assert handle["ws_url"].endswith(handle["task_id"])

        body = _poll_until_done(client, handle["task_id"])

    assert body["status"] == "completed"
    assert body["review"] == {"reviewed": 1, "rejected": 0}
    st = body["sub_tasks"][0]
    assert st["metadata"]["review"]["verdict"] == "pass"


def test_status_running_while_in_flight(client):
    """执行中查询 status → 200 + running（不再 404）"""
    async def _slow_execute(**kwargs):
        await asyncio.sleep(0.4)
        return _make_result()

    executor = cluster_mod.get_cluster_executor()
    with patch.object(executor, "execute", new=AsyncMock(side_effect=_slow_execute)):
        resp = client.post("/cluster/execute", json={
            "task_description": "慢任务",
            "sub_tasks": [{"name": "调研", "prompt": "p"}],
        })
        task_id = resp.json()["task_id"]

        running = client.get(f"/cluster/status/{task_id}")
        assert running.status_code == 200
        assert running.json()["status"] == "running"

        body = _poll_until_done(client, task_id)
    assert body["status"] == "completed"


def test_execute_plan_returns_handle(client):
    executor = cluster_mod.get_cluster_executor()
    with patch.object(
        executor, "execute", new=AsyncMock(return_value=_make_result())
    ):
        resp = client.post("/cluster/execute-plan", json={
            "id": "plan-1",
            "name": "测试计划",
            "description": "d",
            "tasks": [{
                "id": "task-0", "name": "调研", "description": "",
                "prompt": "p", "agent_role": "worker", "priority": "normal",
                "depends_on": [],
            }],
            "aggregation_strategy": AggregationStrategy.SYNTHESIZE.value,
        })
        assert resp.status_code == 200, resp.text
        handle = resp.json()
        assert handle["status"] == "running"
        assert handle["plan_id"] == "plan-1"

        body = _poll_until_done(client, handle["task_id"])
    assert body["status"] == "completed"


def test_completed_event_broadcast(client):
    """后台完成时必须广播 completed 终态事件（前端靠它拉取结果）"""
    broadcasts: list[dict] = []

    async def _fake_broadcast(task_id, progress, message, **kw):
        broadcasts.append({"task_id": task_id, "progress": progress, **kw})

    executor = cluster_mod.get_cluster_executor()
    with patch.object(
        executor, "execute", new=AsyncMock(return_value=_make_result())
    ), patch.object(cluster_mod, "broadcast_progress", new=_fake_broadcast):
        resp = client.post("/cluster/execute", json={
            "task_description": "测试任务",
            "sub_tasks": [{"name": "调研", "prompt": "p"}],
        })
        task_id = resp.json()["task_id"]
        _poll_until_done(client, task_id)

    done = [b for b in broadcasts if b.get("event") == "completed"]
    assert done, f"缺少 completed 终态广播: {broadcasts}"
    assert done[0]["status"] == "completed"


def test_failure_broadcast_and_status(client):
    """后台执行异常 → failed 终态广播 + status 可查错误（不静默）"""
    executor = cluster_mod.get_cluster_executor()
    with patch.object(
        executor, "execute", new=AsyncMock(side_effect=RuntimeError("LLM 全部不可用"))
    ):
        resp = client.post("/cluster/execute", json={
            "task_description": "必炸任务",
            "sub_tasks": [{"name": "调研", "prompt": "p"}],
        })
        task_id = resp.json()["task_id"]
        body = _poll_until_done(client, task_id)

    assert body["status"] == "failed"
    assert "LLM 全部不可用" in (body["error"] or "")
