# -*- coding: utf-8 -*-
"""TokenMeter 与 LLM 参数钳制的鲁棒性测试。

覆盖真实 chat 场景中可能遇到的边界与异常输入：
- TokenMeter：异常 context_window / threshold / usage / 非字符串 content
- sanitize_max_tokens：用户瞎填（0 / 负数 / 字符串 / 超模型硬上限 / 天文数字）
- sanitize_temperature：超界 / 非法
"""
from __future__ import annotations

from backend.agent.token_meter import TokenMeter
from backend.services.llm.param_sanitize import (
    MAX_MAX_TOKENS_CAP,
    sanitize_max_tokens,
    sanitize_temperature,
)


# ── TokenMeter 构造健壮性 ────────────────────────────────────

def test_meter_clamps_garbage_context_window():
    assert TokenMeter(context_window=0).context_window >= 512
    assert TokenMeter(context_window=-100).context_window >= 512
    assert TokenMeter(context_window="abc").context_window == 128_000
    assert TokenMeter(context_window=None).context_window == 128_000
    assert TokenMeter(context_window=999_999_999).context_window <= 10_000_000


def test_meter_clamps_garbage_threshold():
    assert TokenMeter(threshold_percent=5.0).threshold_percent == 1.0
    assert TokenMeter(threshold_percent=-1).threshold_percent == 0.05
    assert TokenMeter(threshold_percent="x").threshold_percent is None
    assert TokenMeter(threshold_percent=float("nan")).threshold_percent is None


# ── update_from_response / remaining / should_compress ───────

def test_update_from_response_tracks_usage():
    m = TokenMeter(context_window=10_000, threshold_percent=0.75)
    m.update_from_response({"prompt_tokens": 8000, "completion_tokens": 100, "total_tokens": 8100})
    assert m.should_compress() is True
    assert m.last_prompt_tokens == 8000
    assert m.last_completion_tokens == 100
    assert m.remaining() == 2000


def test_update_from_response_tolerates_garbage():
    m = TokenMeter(context_window=10_000)
    # 全部不应抛异常
    m.update_from_response(None)
    m.update_from_response("not a dict")
    m.update_from_response({"prompt_tokens": "abc"})
    m.update_from_response({"prompt_tokens": -5})
    m.update_from_response({})
    # 状态保持初始
    assert m.last_prompt_tokens == 0
    assert m.should_compress() is False  # 0 tokens 不触发


def test_update_synthesizes_total_when_missing():
    m = TokenMeter()
    m.update_from_response({"prompt_tokens": 100, "completion_tokens": 50})
    assert m.last_total_tokens == 150


def test_should_compress_no_arg_uses_last_prompt():
    m = TokenMeter(context_window=1000, threshold_percent=0.5)
    m.update_from_response({"prompt_tokens": 600})
    assert m.should_compress() is True
    assert m.should_compress(400) is False


def test_remaining_never_negative():
    m = TokenMeter(context_window=1000)
    m.update_from_response({"prompt_tokens": 5000})
    assert m.remaining() == 0
    assert m.remaining(200) == 800


# ── estimate_messages 边界 ────────────────────────────────────

def test_estimate_messages_handles_tool_calls():
    m = TokenMeter()
    plain = [{"role": "assistant", "content": "ok"}]
    with_tool = [{
        "role": "assistant",
        "content": "ok",
        "tool_calls": [{"id": "1", "type": "function",
                        "function": {"name": "web_search", "arguments": '{"query": "hello world test"}'}}],
    }]
    assert m.estimate_messages(with_tool) > m.estimate_messages(plain)


def test_estimate_messages_handles_multimodal_and_garbage():
    m = TokenMeter()
    msgs = [
        {"role": "user", "content": [{"type": "text", "text": "describe"},
                                     {"type": "image_url", "image_url": {"url": "http://x"}}]},
        {"role": "user", "content": {"unexpected": "dict"}},
        {"role": "user", "content": None},
        "not-a-dict-message",  # 非 dict，应跳过
        {"role": "user"},  # 缺 content
    ]
    # 不应抛异常，且返回非负整数
    est = m.estimate_messages(msgs)
    assert isinstance(est, int) and est >= 0


def test_estimate_messages_non_list():
    m = TokenMeter()
    assert m.estimate_messages(None) == 0
    assert m.estimate_messages("garbage") == 0


def test_estimate_text_empty_and_unicode():
    m = TokenMeter()
    assert m.estimate_text("") == 0
    assert m.estimate_text("你好世界") > 0  # 中文
    assert m.estimate_text("hello") > 0    # 英文


# ── sanitize_max_tokens ───────────────────────────────────────

def test_sanitize_max_tokens_garbage_falls_back():
    assert sanitize_max_tokens(None) == 4096
    assert sanitize_max_tokens("") == 4096
    assert sanitize_max_tokens("abc") == 4096
    assert sanitize_max_tokens([1, 2]) == 4096
    assert sanitize_max_tokens(True) == 4096


def test_sanitize_max_tokens_clamps_low():
    assert sanitize_max_tokens(0) == 1
    assert sanitize_max_tokens(-100) == 1
    assert sanitize_max_tokens("-50") == 1


def test_sanitize_max_tokens_accepts_string_numbers():
    assert sanitize_max_tokens("8192") == 8192
    assert sanitize_max_tokens("4096.0") == 4096


def test_sanitize_max_tokens_absolute_cap():
    assert sanitize_max_tokens(999_999_999) == MAX_MAX_TOKENS_CAP


def test_sanitize_max_tokens_model_hard_cap():
    # claude-3-sonnet 硬上限 4096
    assert sanitize_max_tokens(100_000, model="claude-3-sonnet-20240229") == 4096
    assert sanitize_max_tokens(100_000, model="claude-3-5-sonnet-20241022") == 8192
    # 未知模型不受模型 cap 限制
    assert sanitize_max_tokens(100_000, model="some-random-model") == 100_000


def test_sanitize_max_tokens_respects_context_window():
    assert sanitize_max_tokens(200_000, context_window=128_000) == 128_000
    # context_window 垃圾值被忽略
    assert sanitize_max_tokens(8192, context_window="abc") == 8192


# ── sanitize_temperature ─────────────────────────────────────

def test_sanitize_temperature():
    assert sanitize_temperature(None) == 0.7
    assert sanitize_temperature("abc") == 0.7
    assert sanitize_temperature(-1) == 0.0
    assert sanitize_temperature(99) == 2.0
    assert sanitize_temperature("1.5") == 1.5
    assert sanitize_temperature(float("nan")) == 0.7
