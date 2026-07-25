"""cluster 路由透出复核汇总（Phase 2/3 衔接）测试。

/cluster/execute 与 /cluster/execute-plan 响应必须带 review 汇总
（reviewed/rejected 计数），供前端观测面板展示。
"""

from __future__ import annotations

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
    return TestClient(app)


def test_execute_returns_review_summary(client):
    executor = cluster_mod.get_cluster_executor()
    with patch.object(
        executor, "execute", new=AsyncMock(return_value=_make_result())
    ):
        resp = client.post("/cluster/execute", json={
            "task_description": "测试任务",
            "sub_tasks": [{"name": "调研", "prompt": "p"}],
        })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["review"] == {"reviewed": 1, "rejected": 0}
    # 子任务 metadata 里带复核结论（前端徽章数据源）
    st = body["sub_tasks"][0]
    assert st["metadata"]["review"]["verdict"] == "pass"


def test_execute_plan_returns_review_summary(client):
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
    assert resp.json()["review"] == {"reviewed": 1, "rejected": 0}
