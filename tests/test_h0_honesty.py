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

# ─────────── H0.4 返工：cluster 真正跑通（persona + 专属模型） ───────────


def _fake_llm(content: str) -> AsyncMock:
    """构造最小 LLM mock：chat_complete 返回带 content 的响应"""
    from types import SimpleNamespace

    return AsyncMock(
        chat_complete=AsyncMock(return_value=SimpleNamespace(content=content))
    )


@pytest.mark.asyncio
async def test_persona_system_prompt_goes_to_system_message():
    """persona 的 system_prompt 必须进 system 消息，不再被通用提示词顶替"""
    executor = ClusterExecutor()
    llm = _fake_llm("ok")
    with patch("backend.services.llm.LLMServiceFactory") as factory:
        factory.get_service.return_value = llm
        await executor._execute_agent_prompt(
            {"agent_id": "a1", "system_prompt": "你是资深安全审计员"},
            "审查这段代码",
            {},
        )
    messages = llm.chat_complete.await_args.args[0]
    assert messages[0]["role"] == "system"
    assert "资深安全审计员" in messages[0]["content"]
    # Phase 2：user 消息 = 原始 prompt + 交付契约格式要求
    assert messages[1]["role"] == "user"
    assert "审查这段代码" in messages[1]["content"]
    assert "交付格式要求" in messages[1]["content"]


@pytest.mark.asyncio
async def test_model_ref_resolves_to_snapshot_service():
    """model_ref=provider/model 必须走 get_service_for_snapshot，不用全局默认"""
    executor = ClusterExecutor()
    persona_llm = _fake_llm("persona answer")
    with patch("backend.services.llm.LLMServiceFactory") as factory:
        factory.get_service_for_snapshot.return_value = persona_llm
        result = await executor._execute_agent_prompt(
            {"agent_id": "a1", "model_ref": "prov-x/model-y"},
            "p",
            {},
        )
    factory.get_service_for_snapshot.assert_called_once()
    snapshot = factory.get_service_for_snapshot.call_args.args[0]
    assert snapshot["provider_id"] == "prov-x"
    assert snapshot["model"] == "model-y"
    factory.get_service.assert_not_called()
    assert result["result"] == "persona answer"


@pytest.mark.asyncio
async def test_model_ref_resolve_failure_falls_back_to_default():
    """model_ref 解析失败时降级全局默认服务，保证可用"""
    executor = ClusterExecutor()
    default_llm = _fake_llm("default answer")
    with patch("backend.services.llm.LLMServiceFactory") as factory:
        factory.get_service_for_snapshot.side_effect = KeyError("no such provider")
        factory.get_service.return_value = default_llm
        result = await executor._execute_agent_prompt(
            {"agent_id": "a1", "model_ref": "bad/ref"},
            "p",
            {},
        )
    factory.get_service.assert_called_once()
    assert result["result"] == "default answer"


@pytest.mark.asyncio
async def test_no_model_ref_uses_default_service():
    executor = ClusterExecutor()
    default_llm = _fake_llm("default answer")
    with patch("backend.services.llm.LLMServiceFactory") as factory:
        factory.get_service.return_value = default_llm
        result = await executor._execute_agent_prompt({"agent_id": "a1"}, "p", {})
    factory.get_service_for_snapshot.assert_not_called()
    assert result["result"] == "default answer"


@pytest.mark.asyncio
async def test_cluster_happy_path_parallel_personas_and_synthesize():
    """全链路：2 个 persona 并行真实出稿（各自模型）→ 主 LLM 汇总"""
    from backend.agent.cluster_executor import AggregationStrategy, TaskStatus

    coder_llm = _fake_llm("coder 视角答案")
    writer_llm = _fake_llm("writer 视角答案")
    synth_llm = _fake_llm("综合后的最终答案")

    def _snapshot_router(snapshot: dict):
        return {
            "prov-a": coder_llm,
            "prov-b": writer_llm,
        }[snapshot["provider_id"]]

    executor = ClusterExecutor(timeout_seconds=5)
    with patch("backend.services.llm.LLMServiceFactory") as factory:
        factory.get_service_for_snapshot.side_effect = _snapshot_router
        factory.get_service.return_value = synth_llm
        result = await executor.execute(
            task_description="评审这个设计",
            sub_tasks=[
                {
                    "name": "Coder",
                    "prompt": "p1",
                    "agent_config": {"model_ref": "prov-a/m1", "system_prompt": "你是 Coder"},
                },
                {
                    "name": "Writer",
                    "prompt": "p2",
                    "agent_config": {"model_ref": "prov-b/m2", "system_prompt": "你是 Writer"},
                },
            ],
            aggregation_strategy=AggregationStrategy.SYNTHESIZE,
        )

    assert result.status == TaskStatus.COMPLETED
    by_name = {st.name: st for st in result.sub_tasks}
    assert by_name["Coder"].status == TaskStatus.COMPLETED
    assert by_name["Coder"].result["result"] == "coder 视角答案"
    assert by_name["Writer"].result["result"] == "writer 视角答案"
    # 两个 persona 的 system_prompt 各自生效
    coder_msgs = coder_llm.chat_complete.await_args.args[0]
    writer_msgs = writer_llm.chat_complete.await_args.args[0]
    assert "Coder" in coder_msgs[0]["content"]
    assert "Writer" in writer_msgs[0]["content"]
    # 汇总走主 LLM，结果带 synthesized
    assert result.aggregated_result["synthesized"] == "综合后的最终答案"
