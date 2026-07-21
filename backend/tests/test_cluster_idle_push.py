"""cluster 模式状态推送回归测试。

修复的 bug：复杂任务触发 auto-cluster 后，_execute_cluster_parallel 在
run() 中提前 return，跳过尾部统一的 idle 推送；且其内部只持久化结果、
不推任何状态。导致前端气泡一直停在「思考中」，直到用户手动停止才触发
idle 落盘、结果才显示。

本测试断言：cluster 的两条提前 return 路径（成功 / 失败）都会显式补推
终态（idle / error），前端据此退出流式状态并展示结果。
"""
import asyncio
import sys
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.agent.loop import NexusAgentLoop


def _make_loop():
    """构造最小 NexusAgentLoop（绕过 __init__ 重依赖），捕获状态推送。"""
    loop = NexusAgentLoop.__new__(NexusAgentLoop)
    loop.progress_sink = None
    calls: list[tuple[str, str]] = []

    async def fake_push(session_id, state, detail):
        calls.append((state, detail))

    loop._push_status = fake_push
    loop._persist_final_response = AsyncMock(return_value=None)
    loop._emit_progress = AsyncMock(return_value=None)
    return loop, calls


class _S:
    def __init__(self, v):
        self.value = v


def _fake_modules(result):
    executor = MagicMock()
    executor.execute = AsyncMock(return_value=result)
    agg = MagicMock()

    class AggStrategy:
        SYNTHESIZE = "synthesize"

    agg.SubTaskResult = MagicMock()
    agg.AggregationStrategy = AggStrategy
    return {
        "backend.agent.cluster_executor": MagicMock(get_cluster_executor=lambda: executor),
        "backend.agent.cluster_aggregator": agg,
    }


_SUB_AGENTS = [
    {"id": "a1", "name": "审查员", "description": "代码审查", "system_prompt": "x", "model_ref": "m", "icon": "🔍"},
    {"id": "a2", "name": "研究员", "description": "调研", "system_prompt": "x", "model_ref": "m", "icon": "📚"},
]


def test_cluster_success_pushes_idle():
    """成功路径：持久化结果后必须推 idle，否则前端气泡卡「思考中」。"""

    class OkSub:
        name = "审查员"
        status = _S("completed")
        result = {"result": "有空指针"}

    class OkResult:
        status = _S("completed")
        sub_tasks = [OkSub()]
        aggregated_result = {"synthesized": "综合结论"}
        error = None

    loop, calls = _make_loop()
    with patch.dict(sys.modules, _fake_modules(OkResult())):
        out = asyncio.run(loop._execute_cluster_parallel("审查代码", _SUB_AGENTS, uuid.uuid4()))

    assert out and "集群协作结果" in out
    assert loop._persist_final_response.called
    states = [s for s, _ in calls]
    assert "idle" in states, f"成功路径必须推 idle, 实际={states}"


def test_cluster_failure_pushes_error():
    """失败路径：必须推 error，让前端 setIsStreaming(false) 退出流式。"""

    class FailResult:
        status = _S("failed")
        sub_tasks = []
        aggregated_result = None
        error = "子代理超时"

    loop, calls = _make_loop()
    with patch.dict(sys.modules, _fake_modules(FailResult())):
        asyncio.run(loop._execute_cluster_parallel("审查代码", _SUB_AGENTS, uuid.uuid4()))

    states = [s for s, _ in calls]
    assert "error" in states, f"失败路径必须推 error, 实际={states}"
