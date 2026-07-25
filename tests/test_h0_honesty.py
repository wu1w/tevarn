"""H0 止血回归测试：诚实口径 / 虚词误触发 / 禁模拟成功。

对应 docs/ROADMAP_NEXT_PHASE.md §5.0：
- H0.2 默认 agent_auto_cluster=false
- H0.3 复杂度词表去除连词类虚词，阈值提升，固定中文句集不误触发
- H0.4 子代理 LLM 失败禁止返回「模拟结果」，必须明确失败
"""

from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock, patch

import pytest

from backend.agent.cluster_executor import ClusterExecutor
from backend.agent.loop import NexusAgentLoop
from backend.core.config import Settings

# _analyze_task_complexity 不依赖实例状态，用 cast 绕开构造整个 Loop
_LOOP = cast(NexusAgentLoop, None)


async def _complexity(text: str) -> float:
    return await NexusAgentLoop._analyze_task_complexity(_LOOP, text)


# ─────────── H0.2 默认关闭 auto-cluster ───────────


def test_auto_cluster_default_off():
    assert Settings().agent_auto_cluster is False


# ─────────── H0.3 固定中文句集不误触发 ───────────

# 日常短句：含连词类虚词（和/与/以及/同时/然后），但绝不是多步复杂任务
CASUAL_SENTENCES = [
    "今天天气怎么样？",
    "帮我写一句生日祝福",
    "苹果和香蕉哪个热量高？",
    "你好，介绍一下你自己",
    "把这句话翻译成英文：我喜欢编程",
    "今天我和朋友吃了火锅，很开心",
    "先洗碗然后拖地，你说哪个先来？",
    "我与他同时到达了车站",
    "这个单词怎么读？",
    "给我讲个笑话吧",
]

# 真正复杂的多步任务：应当得分显著高于日常句
COMPLEX_SENTENCES = [
    "分析并比较三个数据库架构设计方案，评估性能与成本，给出详细的优化建议和实施计划",
    "研究多个机器学习模型的训练与推理流程，深入调查批量部署的最佳实践并创建完整的测试报告文档",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("text", CASUAL_SENTENCES)
async def test_casual_sentences_below_cluster_threshold(text: str):
    score = await _complexity(text)
    assert score < 0.8, f"日常句误触发 auto-cluster: {text!r} score={score:.2f}"


@pytest.mark.asyncio
@pytest.mark.parametrize("text", COMPLEX_SENTENCES)
async def test_complex_sentences_score_higher_than_casual(text: str):
    complex_score = await _complexity(text)
    casual_scores = [
        await _complexity(t) for t in CASUAL_SENTENCES
    ]
    assert complex_score > max(casual_scores), (
        f"复杂句得分未超过所有日常句: {text!r} score={complex_score:.2f}"
    )


@pytest.mark.asyncio
async def test_filler_words_contribute_nothing():
    # 同一句话加不加虚词，得分必须一致（虚词已不在词表）
    base = "分析一下这个方案"
    with_fillers = "分析一下这个方案，然后接着再总结一下"
    score_base = await _complexity(base)
    score_fillers = await _complexity(with_fillers)
    assert abs(score_fillers - score_base) < 0.25  # 仅允许长度指标带来的微小差异


# ─────────── H0.4 禁止模拟成功 ───────────


@pytest.mark.asyncio
async def test_llm_failure_raises_instead_of_simulated_result():
    executor = ClusterExecutor()
    with patch("backend.services.llm.LLMServiceFactory") as factory:
        factory.get_service.return_value = AsyncMock(
            chat_complete=AsyncMock(side_effect=ConnectionError("llm down"))
        )
        with pytest.raises(RuntimeError, match="LLM call failed"):
            await executor._execute_agent_prompt(
                {"agent_id": "t"}, "test prompt", {}
            )


@pytest.mark.asyncio
async def test_llm_failure_marks_task_failed_not_completed():
    executor = ClusterExecutor(timeout_seconds=5)
    with patch("backend.services.llm.LLMServiceFactory") as factory:
        factory.get_service.return_value = AsyncMock(
            chat_complete=AsyncMock(side_effect=ConnectionError("llm down"))
        )
        result = await executor.execute(
            task_description="test",
            sub_tasks=[{"name": "t1", "prompt": "p1"}],
        )
    from backend.agent.cluster_executor import TaskStatus

    assert result.sub_tasks[0].status == TaskStatus.FAILED
    assert result.sub_tasks[0].result is None or (
        isinstance(result.sub_tasks[0].result, dict)
        and result.sub_tasks[0].result.get("status") != "simulated"
    )
    assert "模拟结果" not in str(result.to_dict())
