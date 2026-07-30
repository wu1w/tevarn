"""Phase 1.2 并发/超时/压缩关账回归。

映射（第二轮审计条目 → 真实代码）：
- L2-H1 tool_execution 超时竞态 → tool_round._await_with_timeout_cleanup
- L2-H2 context 压缩递归 → context_pipeline max_l5_retries + hard truncate
- L2-M2/M3 checkpoint/goal 并发 → test_session_config_merge（既有）
- L2-H3 orchestrator _state_lock → N/A（本仓库无该模块，见 DEV_PLAN 备注）
"""
from __future__ import annotations

import asyncio

import pytest

from backend.agent.context_pipeline import PipelineContextEngine
from backend.agent.phases.tool_round import _await_with_timeout_cleanup


@pytest.mark.asyncio
async def test_timeout_cancels_and_awaits_underlying_task():
    """超时后底层 task 必须进入 cancelled/done，不得残留。"""
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def slow():
        started.set()
        try:
            await asyncio.sleep(30)
            return "done"
        except asyncio.CancelledError:
            cancelled.set()
            raise

    with pytest.raises(asyncio.TimeoutError):
        await _await_with_timeout_cleanup(slow(), 0.05)

    assert started.is_set()
    for _ in range(10):
        if cancelled.is_set():
            break
        await asyncio.sleep(0)
    assert cancelled.is_set(), "underlying coroutine must observe CancelledError"


@pytest.mark.asyncio
async def test_timeout_cleanup_does_not_leak_on_success():
    async def fast():
        return 42

    assert await _await_with_timeout_cleanup(fast(), 1.0) == 42


def test_hard_truncate_helper_preserves_head_tail():
    """直接测 _hard_truncate（不依赖 settings/LLM）。"""
    eng = PipelineContextEngine()
    eng.protect_first_n = 2
    eng.protect_last_n = 2
    eng.max_l5_retries = 3
    messages = [{"role": "user", "content": f"m{i}"} for i in range(20)]
    out, n = eng._hard_truncate(messages)
    assert n == 16
    assert len(out) == 5  # 2 head + marker + 2 tail
    assert out[0]["content"] == "m0"
    assert out[-1]["content"] == "m19"
    assert "hard-truncated" in out[2]["content"]


@pytest.mark.asyncio
async def test_hard_truncate_layer_applied(monkeypatch):
    # compress() 每轮从 settings 同步 meter——测试必须压低全局窗口/阈值
    from backend.core import config as cfg
    from backend.agent import context_pipeline as cp

    monkeypatch.setattr(cfg.settings, "context_window", 512, raising=False)
    monkeypatch.setattr(cfg.settings, "context_threshold_percent", 0.05, raising=False)
    monkeypatch.setattr(cp.settings, "context_window", 512, raising=False)
    monkeypatch.setattr(cp.settings, "context_threshold_percent", 0.05, raising=False)

    eng = PipelineContextEngine()
    eng.context_length = 512
    eng.meter.context_window = 512
    eng.meter.threshold_percent = 0.05
    eng.enable_l1 = True
    eng.enable_l3 = True
    eng.enable_l5 = False
    eng.protect_first_n = 2
    eng.protect_last_n = 2

    messages = [{"role": "user", "content": ("word " * 80) + str(i)} for i in range(30)]
    out, meta = await eng.compress(messages, allow_l5=False)
    assert len(out) < len(messages)
    assert meta.get("hard_truncated") or any(
        str(x).startswith("HARD:") for x in (meta.get("layers") or [])
    ), meta
    assert out[0]["content"] == messages[0]["content"]
    assert out[-1]["content"] == messages[-1]["content"]


def test_max_l5_retries_field_defaults():
    eng = PipelineContextEngine()
    assert eng.max_l5_retries == 3
    assert eng._l5_attempts_run == 0
