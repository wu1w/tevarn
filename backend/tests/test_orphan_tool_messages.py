# -*- coding: utf-8 -*-
"""孤儿 tool 消息导致严格网关（Kimi K3）400 的回归测试。

根因：历史压缩（L3/L5）会剥掉 assistant 的 tool_calls 或丢弃 tool 消息，
但可能残留"孤儿 tool 消息"——其 tool_call_id 在序列中没有对应的
assistant.tool_calls。严格 OpenAI 兼容网关会因 tool 配对错乱返回 400
（Kimi 文案误导为 model 校验错误）。

修复（双保险）：
1. services/llm/openai_compatible._sanitize_messages_for_api 发送前丢弃孤儿 tool 消息
2. agent/context_pipeline 的 L3/L5 压缩时同步剔除孤儿 tool 消息
"""

import pytest

from backend.services.llm.openai_compatible import OpenAICompatibleService
from backend.agent.context_pipeline import PipelineContextEngine


def _sanitize(messages):
    return OpenAICompatibleService._sanitize_messages_for_api(messages)


# ── sanitize 兜底 ──────────────────────────────────────────────────────────

def test_sanitize_drops_orphan_tool_message():
    """孤儿 tool 消息（无对应 assistant.tool_calls）必须被丢弃。"""
    messages = [
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": "做事"},
        # assistant 的 tool_calls 已被压缩剥成纯文本
        {"role": "assistant", "content": "[tool calls omitted x2]"},
        # 残留的孤儿 tool 消息
        {"role": "tool", "tool_call_id": "call_orphan1", "content": "r1"},
        {"role": "tool", "tool_call_id": "call_orphan2", "content": "r2"},
        {"role": "user", "content": "继续"},
    ]
    out = _sanitize(messages)
    tool_msgs = [m for m in out if m.get("role") == "tool"]
    assert tool_msgs == [], f"孤儿 tool 消息应被丢弃，实际残留: {tool_msgs}"


def test_sanitize_keeps_valid_tool_pairing():
    """正常配对的 assistant.tool_calls + tool 消息必须完整保留。"""
    messages = [
        {"role": "user", "content": "查天气"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_valid1",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": '{"city":"北京"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_valid1", "content": "晴 25°C"},
    ]
    out = _sanitize(messages)
    tool_msgs = [m for m in out if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "call_valid1"
    # assistant 的 tool_calls 也应保留
    asst = [m for m in out if m.get("role") == "assistant"][0]
    assert asst["tool_calls"][0]["id"] == "call_valid1"


def test_sanitize_mixed_orphan_and_valid():
    """混合场景：孤儿被丢、合法配对保留。"""
    messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_keep",
                    "type": "function",
                    "function": {"name": "f", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_keep", "content": "ok"},
        # 这个是孤儿
        {"role": "tool", "tool_call_id": "call_gone", "content": "stale"},
    ]
    out = _sanitize(messages)
    ids = [m.get("tool_call_id") for m in out if m.get("role") == "tool"]
    assert ids == ["call_keep"], f"应只保留合法配对，实际: {ids}"


def test_sanitize_strips_orphan_tool_calls():
    """assistant.tool_calls 缺对应 tool_result 时，必须剥掉孤儿 tool_calls。"""
    messages = [
        {"role": "user", "content": "做事"},
        {
            "role": "assistant",
            "content": "我来调用工具",
            "tool_calls": [
                {"id": "call_has_result", "type": "function", "function": {"name": "a", "arguments": "{}"}},
                {"id": "call_no_result", "type": "function", "function": {"name": "b", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "call_has_result", "content": "done"},
        # call_no_result 没有对应 tool 消息 → 孤儿 tool_call
    ]
    out = _sanitize(messages)
    asst = [m for m in out if m.get("role") == "assistant"][0]
    kept_ids = [tc["id"] for tc in asst.get("tool_calls", [])]
    assert kept_ids == ["call_has_result"], f"孤儿 tool_call 应被剥掉，实际保留: {kept_ids}"


def test_sanitize_orphan_tool_calls_empty_turn_placeholder():
    """剥光 tool_calls 后 content 为空时，必须填占位符避免空 assistant turn。"""
    messages = [
        {"role": "user", "content": "做事"},
        {
            "role": "assistant",
            "content": None,  # content 为空
            "tool_calls": [
                {"id": "call_lone", "type": "function", "function": {"name": "x", "arguments": "{}"}},
            ],
        },
        # 没有任何 tool 消息 → tool_calls 全是孤儿，剥光后 content 仍空
    ]
    out = _sanitize(messages)
    asst = [m for m in out if m.get("role") == "assistant"][0]
    assert "tool_calls" not in asst, "孤儿 tool_calls 应被全部剥掉"
    assert asst.get("content"), "剥光后 content 不能为空（防止空 turn 被网关拒绝）"


def test_sanitize_valid_pairing_not_stripped():
    """正常配对的 tool_calls 绝不能被误剥。"""
    messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call_ok", "type": "function", "function": {"name": "f", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "call_ok", "content": "result"},
    ]
    out = _sanitize(messages)
    asst = [m for m in out if m.get("role") == "assistant"][0]
    assert asst.get("tool_calls"), "合法 tool_calls 不应被剥掉"
    assert asst["tool_calls"][0]["id"] == "call_ok"


# ── L3 源头 ────────────────────────────────────────────────────────────────

def _make_l3_messages(tool_count: int = 6):
    """构造能触发 L3 的消息序列（mid 区段 tool >= 4）。

    protect_first_n=3, protect_last_n=12。
    中间塞入足够多的 assistant(tool_calls)+tool 对，使 mid 区段 tool >= 4。
    关键：让某些 assistant 的 tool 结果落在 tail 保护区，制造潜在孤儿。
    """
    msgs = [{"role": "system", "content": "sys"}]
    # head 保护区（前 3 条非 system）
    msgs.append({"role": "user", "content": "开始任务"})
    msgs.append({"role": "assistant", "content": "好的"})
    msgs.append({"role": "user", "content": "第一步"})
    # mid 区段：多轮工具调用
    for i in range(tool_count):
        msgs.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"call_mid_{i}",
                        "type": "function",
                        "function": {"name": f"tool_{i}", "arguments": "{}"},
                    }
                ],
            }
        )
        msgs.append({"role": "tool", "tool_call_id": f"call_mid_{i}", "content": f"result_{i}"})
    # tail 保护区（最后 12 条非 system），含一个引用了 mid tool_call 的孤儿候选
    for j in range(11):
        msgs.append({"role": "user", "content": f"后续 {j}"})
    # 一个 tool 消息引用 mid 区段已被剥掉的 tool_call_id → 应被 L3 剔除
    msgs.append({"role": "tool", "tool_call_id": "call_mid_0", "content": "late result"})
    return msgs


def test_l3_no_orphan_tool_after_compact():
    """L3 压缩后，序列中不得存在孤儿 tool 消息。"""
    engine = PipelineContextEngine()
    engine.protect_first_n = 3
    engine.protect_last_n = 12
    messages = _make_l3_messages(tool_count=6)
    out, dropped = engine._l3_microcompact(messages)
    # 确认 L3 真的触发了压缩
    assert dropped >= 3, f"L3 应触发压缩（dropped>=3），实际 dropped={dropped}"

    # 收集 out 中所有声明的 tool_call_id
    declared = set()
    for m in out:
        if m.get("role") == "assistant":
            for tc in m.get("tool_calls") or []:
                if isinstance(tc, dict) and tc.get("id"):
                    declared.add(str(tc["id"]))
    # 检查孤儿
    orphans = [
        m.get("tool_call_id")
        for m in out
        if m.get("role") == "tool"
        and m.get("tool_call_id")
        and str(m["tool_call_id"]) not in declared
    ]
    assert orphans == [], f"L3 压缩后仍存在孤儿 tool 消息: {orphans}"


# ── L5 源头 ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_l5_no_orphan_tool_after_compact(monkeypatch):
    """L5 压缩后，tail 中不得残留孤儿 tool 消息。"""
    engine = PipelineContextEngine()
    engine.protect_last_n = 6

    # 避免真实 LLM 调用：stub 掉摘要
    async def _fake_summarize(self, transcript, focus_line):
        return "历史摘要"

    monkeypatch.setattr(PipelineContextEngine, "_llm_summarize", _fake_summarize)

    messages = [{"role": "system", "content": "sys"}]
    # head：assistant 带 tool_calls（将被压缩成摘要）
    for i in range(5):
        messages.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"call_head_{i}",
                        "type": "function",
                        "function": {"name": f"t{i}", "arguments": "{}"},
                    }
                ],
            }
        )
        messages.append({"role": "tool", "tool_call_id": f"call_head_{i}", "content": f"r{i}"})
    messages.append({"role": "user", "content": "中间"})
    # tail（最后 6 条）：混入一个引用 head tool_call_id 的孤儿 tool 消息
    messages.append({"role": "user", "content": "t1"})
    messages.append({"role": "assistant", "content": "a1"})
    messages.append({"role": "user", "content": "t2"})
    messages.append({"role": "tool", "tool_call_id": "call_head_0", "content": "orphan"})
    messages.append({"role": "user", "content": "t3"})
    messages.append({"role": "user", "content": "t4"})

    out, meta = await engine._l5_auto_compact(messages, focus_topic=None, session_id=None)
    assert meta.get("applied"), "L5 应触发压缩"

    declared = set()
    for m in out:
        if m.get("role") == "assistant":
            for tc in m.get("tool_calls") or []:
                if isinstance(tc, dict) and tc.get("id"):
                    declared.add(str(tc["id"]))
    orphans = [
        m.get("tool_call_id")
        for m in out
        if m.get("role") == "tool"
        and m.get("tool_call_id")
        and str(m["tool_call_id"]) not in declared
    ]
    assert orphans == [], f"L5 压缩后仍存在孤儿 tool 消息: {orphans}"
