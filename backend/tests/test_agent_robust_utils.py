"""续跑话术 / 瞬态错误 / 重试工具测试。"""
from __future__ import annotations

import asyncio

import pytest

from backend.agent.robust import (
    async_retry,
    is_continue_phrase,
    is_transient_llm_error,
)


def test_continue_phrases():
    assert is_continue_phrase("请继续")
    assert is_continue_phrase("continue")
    assert is_continue_phrase("接着做")
    assert not is_continue_phrase("")
    assert not is_continue_phrase("请帮我写一个很长的方案" + "x" * 100)


def test_transient_errors():
    assert is_transient_llm_error(RuntimeError("502 Bad Gateway"))
    assert is_transient_llm_error(TimeoutError("timed out"))
    assert is_transient_llm_error(RuntimeError("rate limit exceeded"))
    assert not is_transient_llm_error(ValueError("invalid schema"))


@pytest.mark.asyncio
async def test_async_retry_succeeds_second_try():
    state = {"n": 0}

    async def flaky():
        state["n"] += 1
        if state["n"] < 2:
            raise RuntimeError("503 temporary")
        return "ok"

    out = await async_retry(
        flaky,
        attempts=3,
        base_delay=0.01,
        max_delay=0.02,
        retry_if=is_transient_llm_error,
        label="test",
    )
    assert out == "ok"
    assert state["n"] == 2


@pytest.mark.asyncio
async def test_async_retry_no_retry_on_permanent():
    state = {"n": 0}

    async def boom():
        state["n"] += 1
        raise ValueError("bad request")

    with pytest.raises(ValueError):
        await async_retry(
            boom,
            attempts=3,
            base_delay=0.01,
            retry_if=is_transient_llm_error,
        )
    assert state["n"] == 1
