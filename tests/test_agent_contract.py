"""Phase 2：Agent Contract + Review Loop 测试

- 交付物契约解析（JSON 合规 / 降级 / 签名防篡改）
- reviewer 复核结论解析（JSON 数组 / 不可解析默认 pass 不误杀）
- 集成：pass / revise 返工 / reject 剔除 / reviewer 挂掉不阻塞
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.agent.agent_contract import (
    AgentDeliverable,
    build_review_prompt,
    parse_deliverable,
    parse_review_verdicts,
    sign_deliverable,
)
from backend.agent.cluster_executor import AggregationStrategy, ClusterExecutor


def _resp(content: str):
    return SimpleNamespace(content=content)


class _ScriptedLLM:
    """chat_complete 按剧本出内容"""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[list[dict]] = []

    async def chat_complete(self, messages):
        self.calls.append(messages)
        content = self.script.pop(0) if self.script else "(剧本耗尽)"
        return _resp(content)


# ─────────── 契约解析 ───────────


def test_parse_deliverable_json_block():
    raw = '前言\n```json\n{"content": "答案是 42", "confidence": 0.9, "claims": ["宇宙答案=42"]}\n```\n后记'
    d = parse_deliverable(raw, agent_id="a1", task_id="t1", model="openai/gpt-4o")
    assert d.content == "答案是 42"
    assert d.confidence == 0.9
    assert d.claims == ["宇宙答案=42"]
    assert d.model == "openai/gpt-4o"
    assert d.verify()  # 签名自洽


def test_parse_deliverable_fallback_plain_text():
    d = parse_deliverable("就是一段普通回答，没有 JSON", agent_id="a1", task_id="t1")
    assert d.content == "就是一段普通回答，没有 JSON"
    assert d.confidence is None
    assert d.claims == []
    assert d.verify()


def test_deliverable_signature_tamper_detection():
    d = parse_deliverable('{"content": "原版", "confidence": 0.5}', agent_id="a", task_id="t")
    assert d.verify()
    d.content = "被下游篡改的内容"
    assert not d.verify()  # 签名不匹配 → 检测到篡改


def test_parse_deliverable_confidence_clamped():
    d = parse_deliverable('{"content": "x", "confidence": 7.5}', agent_id="a", task_id="t")
    assert d.confidence == 1.0


# ─────────── 复核结论解析 ───────────


def test_parse_review_verdicts_array():
    raw = json.dumps([
        {"task_id": "t1", "verdict": "pass", "score": 0.95, "issues": [], "suggestion": None},
        {"task_id": "t2", "verdict": "revise", "score": 0.4, "issues": ["事实错误"], "suggestion": "改成 X"},
    ], ensure_ascii=False)
    verdicts = parse_review_verdicts(raw, ["t1", "t2"])
    assert verdicts["t1"].verdict == "pass"
    assert verdicts["t2"].verdict == "revise"
    assert verdicts["t2"].issues == ["事实错误"]
    assert verdicts["t2"].suggestion == "改成 X"


def test_parse_review_verdicts_garbage_defaults_pass():
    """reviewer 输出不可解析 → 默认 pass，复核失败不应阻塞交付（不误杀）"""
    verdicts = parse_review_verdicts("我完全不知道怎么输出 JSON", ["t1", "t2"])
    assert verdicts["t1"].verdict == "pass"
    assert verdicts["t2"].verdict == "pass"


def test_build_review_prompt_contains_task_and_deliverables():
    d = AgentDeliverable(agent_id="a", task_id="t1", content="内容", claims=["断言1"])
    prompt = build_review_prompt("原始任务描述", [d])
    assert "原始任务描述" in prompt
    assert "断言1" in prompt
    assert "t1" in prompt


# ─────────── 集成：review loop ───────────

_SUBTASKS = [
    {
        "id": "t1",
        "name": "调研",
        "prompt": "调研 X",
        "agent_config": {
            "agent_id": "researcher",
            "system_prompt": "你是调研员",
            "model_ref": "prov/model-a",
        },
    },
    {
        "id": "t2",
        "name": "写作",
        "prompt": "写总结",
        "agent_config": {
            "agent_id": "writer",
            "system_prompt": "你是写手",
            "model_ref": "prov/model-b",
        },
    },
]


def _patch_llms(sub_llm, reviewer_llm):
    """sub-agent 与 reviewer/synthesizer 共用 get_service 时的打补丁组合"""
    return (
        patch(
            "backend.services.llm.LLMServiceFactory.get_service_for_snapshot",
            return_value=sub_llm,
        ),
        patch(
            "backend.services.llm.LLMServiceFactory.get_service",
            return_value=reviewer_llm,
        ),
    )


@pytest.mark.asyncio
async def test_review_pass_path_records_verdict():
    sub_llm = _ScriptedLLM([
        '{"content": "调研结果A", "confidence": 0.8, "claims": ["A1"]}',
        '{"content": "写作结果B", "confidence": 0.7, "claims": ["B1"]}',
    ])
    reviewer_llm = _ScriptedLLM([
        # 复核：全部 pass
        json.dumps([
            {"task_id": "t1", "verdict": "pass", "score": 0.9},
            {"task_id": "t2", "verdict": "pass", "score": 0.85},
        ]),
        # 综合
        "综合后的最终答案",
    ])
    p1, p2 = _patch_llms(sub_llm, reviewer_llm)
    with p1, p2:
        ex = ClusterExecutor()
        result = await ex.execute("总任务", _SUBTASKS, AggregationStrategy.SYNTHESIZE)

    assert result.status.value == "completed"
    assert result.metadata["review"] == {"reviewed": 2, "rejected": 0}
    t1 = next(t for t in result.sub_tasks if t.id == "t1")
    assert t1.metadata["review"]["verdict"] == "pass"
    assert t1.metadata["deliverable"]["confidence"] == 0.8
    assert t1.metadata["deliverable"]["signature"]
    agg = result.aggregated_result
    assert agg["synthesized"] == "综合后的最终答案"
    assert agg["review_notes"] and agg["rejected"] == []


@pytest.mark.asyncio
async def test_review_revise_triggers_single_rework():
    sub_llm = _ScriptedLLM([
        '{"content": "初稿A", "confidence": 0.5}',
        '{"content": "结果B", "confidence": 0.7}',
        # t1 返工后的重新交付
        '{"content": "修订稿A", "confidence": 0.9}',
    ])
    reviewer_llm = _ScriptedLLM([
        # 第一轮复核：t1 revise / t2 pass
        json.dumps([
            {"task_id": "t1", "verdict": "revise", "score": 0.3,
             "issues": ["数字对不上"], "suggestion": "核对来源"},
            {"task_id": "t2", "verdict": "pass", "score": 0.9},
        ]),
        # 第二轮复核（返工后）：全部 pass
        json.dumps([
            {"task_id": "t1", "verdict": "pass", "score": 0.9},
            {"task_id": "t2", "verdict": "pass", "score": 0.9},
        ]),
        # 综合
        "最终综合",
    ])
    p1, p2 = _patch_llms(sub_llm, reviewer_llm)
    with p1, p2:
        ex = ClusterExecutor()
        result = await ex.execute("总任务", _SUBTASKS, AggregationStrategy.SYNTHESIZE)

    t1 = next(t for t in result.sub_tasks if t.id == "t1")
    # t1 被返工一次：子代理共调用 3 次
    assert len(sub_llm.calls) == 3
    # 返工 prompt 带了复核意见
    rework_prompt = sub_llm.calls[2][-1]["content"]
    assert "复核返工要求" in rework_prompt and "数字对不上" in rework_prompt
    # 返工后交付物更新 + 最终 pass
    assert t1.metadata["deliverable"]["content"] == "修订稿A"
    assert t1.metadata["review"]["verdict"] == "pass"
    assert t1.metadata["review_rounds"] == 1
    assert result.aggregated_result["synthesized"] == "最终综合"


@pytest.mark.asyncio
async def test_review_reject_excluded_from_synthesis():
    sub_llm = _ScriptedLLM([
        '{"content": "好结果", "confidence": 0.9}',
        '{"content": "完全跑题的回答", "confidence": 0.2}',
    ])
    reviewer_llm = _ScriptedLLM([
        json.dumps([
            {"task_id": "t1", "verdict": "pass", "score": 0.9},
            {"task_id": "t2", "verdict": "reject", "score": 0.1, "issues": ["答非所问"]},
        ]),
        "只用了好结果的综合",
    ])
    p1, p2 = _patch_llms(sub_llm, reviewer_llm)
    with p1, p2:
        ex = ClusterExecutor()
        result = await ex.execute("总任务", _SUBTASKS, AggregationStrategy.SYNTHESIZE)

    t2 = next(t for t in result.sub_tasks if t.id == "t2")
    assert t2.metadata["rejected"] is True
    agg = result.aggregated_result
    assert agg["rejected"] == ["写作"]
    # 综合输入里只剩 t1 的结果
    assert len(agg["raw_results"]) == 1
    assert "好结果" in str(agg["raw_results"][0]["result"])
    assert result.metadata["review"] == {"reviewed": 2, "rejected": 1}


@pytest.mark.asyncio
async def test_reviewer_failure_does_not_block_delivery():
    """reviewer 挂掉 → 复核跳过、交付不被阻塞（诚实降级）"""
    sub_llm = _ScriptedLLM([
        '{"content": "结果A", "confidence": 0.8}',
        '{"content": "结果B", "confidence": 0.8}',
    ])
    p1 = patch(
        "backend.services.llm.LLMServiceFactory.get_service_for_snapshot",
        return_value=sub_llm,
    )
    # get_service 第一次（reviewer）抛错 → 跳过复核；第二次（综合）正常
    p2 = patch(
        "backend.services.llm.LLMServiceFactory.get_service",
        side_effect=[RuntimeError("reviewer down"), _ScriptedLLM(["降级综合"])],
    )
    with p1, p2:
        ex = ClusterExecutor()
        result = await ex.execute("总任务", _SUBTASKS, AggregationStrategy.SYNTHESIZE)

    assert result.status.value == "completed"
    # 复核被跳过（无 review 元数据），但交付物契约仍解析了
    t1 = next(t for t in result.sub_tasks if t.id == "t1")
    assert "deliverable" in t1.metadata
    assert "review" not in t1.metadata
    assert result.aggregated_result["synthesized"] == "降级综合"


@pytest.mark.asyncio
async def test_review_disabled_by_setting():
    sub_llm = _ScriptedLLM(["结果A", "结果B"])
    reviewer_llm = _ScriptedLLM(["综合"])
    p1, p2 = _patch_llms(sub_llm, reviewer_llm)
    with p1, p2, patch(
        "backend.core.config.settings.cluster_review_enabled", False
    ):
        ex = ClusterExecutor()
        result = await ex.execute("总任务", _SUBTASKS, AggregationStrategy.SYNTHESIZE)

    # 复核未运行：reviewer 只被调了综合那一次
    assert len(reviewer_llm.calls) == 1
    t1 = next(t for t in result.sub_tasks if t.id == "t1")
    assert "review" not in t1.metadata
