"""Tests for PipelineContextEngine thrashing guard (L5 circuit breaker).

压缩风暴熔断：180s 内 L5(hard compact) >= 3 次 → 进入冷却期，
冷却期内禁止再砍对话（只跑 L1/L3 micro），防止上下文被打到不可用。
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest

from backend.agent.context_pipeline import PipelineContextEngine


def _mk_msgs(n: int = 8) -> list[dict]:
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "start"}]
    for i in range(n):
        msgs.append({"role": "assistant", "content": f"a{i}"})
        msgs.append({"role": "tool", "content": f"t{i}" * 10, "tool_call_id": str(i)})
    msgs.append({"role": "user", "content": "latest"})
    return msgs


def _force_l5_trigger(eng: PipelineContextEngine) -> None:
    """让 should_compress 恒真，迫使 compress 走到 L5 分支（or 短路即可，无需动阈值）。"""
    eng.meter.should_compress = lambda *a, **k: True  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_l5_records_event_on_trigger() -> None:
    eng = PipelineContextEngine()
    _force_l5_trigger(eng)
    assert eng._l5_events == []
    with patch.object(eng, "_l5_auto_compact", new=AsyncMock(return_value=(_mk_msgs(), {"applied": True}))):
        await eng.compress(_mk_msgs(), current_tokens=10_000)
    assert len(eng._l5_events) == 1


@pytest.mark.asyncio
async def test_thrash_trips_after_max_events_in_window() -> None:
    eng = PipelineContextEngine()
    eng.thrash_max_events = 3
    _force_l5_trigger(eng)
    l5_mock = AsyncMock(return_value=(_mk_msgs(), {"applied": True}))
    with patch.object(eng, "_l5_auto_compact", new=l5_mock):
        # 连续 3 次触发（都在窗口内）→ 第 3 次后进入熔断
        for _ in range(3):
            await eng.compress(_mk_msgs(), current_tokens=10_000)
    # 熔断后窗口被清空，冷却时间已设置
    assert eng._thrash_until > time.monotonic()
    assert eng._thrash_active() is True
    assert eng._l5_events == []
    assert l5_mock.await_count == 3


@pytest.mark.asyncio
async def test_thrash_suppresses_l5_during_cooldown() -> None:
    eng = PipelineContextEngine()
    eng.thrash_max_events = 3
    eng.thrash_cooldown_sec = 300
    _force_l5_trigger(eng)
    l5_mock = AsyncMock(return_value=(_mk_msgs(), {"applied": True}))
    with patch.object(eng, "_l5_auto_compact", new=l5_mock):
        for _ in range(3):
            await eng.compress(_mk_msgs(), current_tokens=10_000)
        assert eng._thrash_active() is True
        # 熔断期内再 compress：L5 必须被抑制
        out, meta = await eng.compress(_mk_msgs(), current_tokens=10_000)
    assert l5_mock.await_count == 3  # 没增加
    assert meta.get("thrash_suppressed_l5") is True


@pytest.mark.asyncio
async def test_l5_events_expire_outside_window() -> None:
    """窗口外的事件不计入熔断。"""
    eng = PipelineContextEngine()
    eng.thrash_max_events = 3
    eng.thrash_window_sec = 180
    now = time.monotonic()
    # 预置 2 个「很久以前」的事件（超出窗口）
    eng._l5_events = [now - 400, now - 300]
    eng._record_l5_and_maybe_trip()
    # 过期事件被剔除，只剩新加的 1 个，不触发熔断
    assert len(eng._l5_events) == 1
    assert eng._thrash_active() is False


@pytest.mark.asyncio
async def test_no_trip_below_threshold() -> None:
    eng = PipelineContextEngine()
    eng.thrash_max_events = 3
    _force_l5_trigger(eng)
    l5_mock = AsyncMock(return_value=(_mk_msgs(), {"applied": True}))
    with patch.object(eng, "_l5_auto_compact", new=l5_mock):
        for _ in range(2):  # 只触发 2 次 < 3
            await eng.compress(_mk_msgs(), current_tokens=10_000)
    assert eng._thrash_active() is False
    assert len(eng._l5_events) == 2
