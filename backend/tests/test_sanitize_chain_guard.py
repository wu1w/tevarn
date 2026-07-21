"""Sanitize 调用链守护测试（防 K2 复发：重构/sync 静默旁路 _sanitize_messages_for_api）。

K2 教训：sanitize 层曾被一次重写静默覆盖丢失，导致 orphan tool pair 400 复发。
本测试不测 sanitize 的正确性（另有专门测试），而是守护「调用链不可旁路」：
每次 chat()/chat_complete() 发送给 API 的 payload，其 messages 必须是
_sanitize_messages_for_api 的返回值，且 sanitize 必须先于 HTTP 发送执行。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from backend.services.llm.openai_compatible import OpenAICompatibleService


def _mk_service() -> OpenAICompatibleService:
    from types import SimpleNamespace

    cfg = SimpleNamespace(
        base_url="http://127.0.0.1:9",  # 不可达，仅用于构造；HTTP 会被 mock
        model="test-model",
        api_key="k",
        max_tokens=16,
        temperature=0.0,
    )
    return OpenAICompatibleService(config=cfg)


class _FakeResp:
    """最小 aiohttp response 替身（chat 非流式分支）。"""

    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def raise_for_status(self):
        return None

    async def json(self) -> dict[str, Any]:
        return {
            "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }


class _FakeSession:
    def __init__(self, captured: dict[str, Any]):
        self._captured = captured

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def post(self, url, json=None, headers=None):
        self._captured["url"] = url
        self._captured["payload"] = json
        return _FakeResp()


@pytest.mark.asyncio
async def test_sanitize_runs_before_http_and_feeds_payload() -> None:
    svc = _mk_service()
    calls: list[str] = []
    captured: dict[str, Any] = {}

    real_sanitize = svc._sanitize_messages_for_api

    def spy_sanitize(cls, messages):
        calls.append("sanitize")
        return real_sanitize(messages)

    def fake_session_factory(*a, **k):
        calls.append("http")
        return _FakeSession(captured)

    # 含 orphan tool 的消息：sanitize 应剔除它
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "hi"},
        {"role": "tool", "content": "ORPHAN", "tool_call_id": "no-match"},  # orphan
        {"role": "assistant", "content": "answer"},
    ]

    with patch.object(
        OpenAICompatibleService, "_sanitize_messages_for_api", spy_sanitize
    ), patch("aiohttp.ClientSession", side_effect=fake_session_factory):
        async for _ in svc.chat(messages, stream=False):
            pass

    # 1. sanitize 被调用，且在 HTTP 发送之前
    assert "sanitize" in calls, "sanitize 未被调用 — 调用链被旁路！"
    assert "http" in calls, "HTTP 未发送（测试环境错误）"
    assert calls.index("sanitize") < calls.index("http"), (
        f"sanitize 必须先于 HTTP 发送，实际顺序: {calls}"
    )

    # 2. payload 的 messages 是 sanitize 的输出（orphan 已被剔除）
    sent = captured["payload"]["messages"]
    sent_roles = [m.get("role") for m in sent]
    assert "ORPHAN" not in [m.get("content") for m in sent], "orphan tool 未被 sanitize 剔除"
    # orphan tool 被剔除后，不应有 content=='ORPHAN' 的 tool 消息
    assert all(m.get("content") != "ORPHAN" for m in sent)


@pytest.mark.asyncio
async def test_chat_complete_also_passes_through_sanitize() -> None:
    """chat_complete 走 chat()，同样必经 sanitize（守护非流式入口）。"""
    svc = _mk_service()
    sanitize_hits: list[int] = []
    captured: dict[str, Any] = {}

    real_sanitize = svc._sanitize_messages_for_api

    def spy_sanitize(cls, messages):
        sanitize_hits.append(1)
        return real_sanitize(messages)

    def fake_session_factory(*a, **k):
        return _FakeSession(captured)

    with patch.object(
        OpenAICompatibleService, "_sanitize_messages_for_api", spy_sanitize
    ), patch("aiohttp.ClientSession", side_effect=fake_session_factory):
        resp = await svc.chat_complete([{"role": "user", "content": "hi"}])

    assert sanitize_hits, "chat_complete 未经过 sanitize — 调用链被旁路！"
    assert resp.content == "ok"


@pytest.mark.asyncio
async def test_sanitize_is_first_transformation_of_messages() -> None:
    """守护锚点：payload['messages'] 与 _sanitize_messages_for_api 输出逐字节一致。

    若未来有人在 sanitize 之后又插入一层未净化变换（K2 同族），此测试会失败。
    """
    svc = _mk_service()
    captured: dict[str, Any] = {}
    sanitize_output: list[dict] = []

    real_sanitize = svc._sanitize_messages_for_api

    def spy_sanitize(cls, messages):
        out = real_sanitize(messages)
        sanitize_output.clear()
        sanitize_output.extend(out)
        return out

    def fake_session_factory(*a, **k):
        return _FakeSession(captured)

    messages = [
        {"role": "user", "content": "hi"},
        {"role": "tool", "content": "ORPHAN", "tool_call_id": "x"},
    ]

    with patch.object(
        OpenAICompatibleService, "_sanitize_messages_for_api", spy_sanitize
    ), patch("aiohttp.ClientSession", side_effect=fake_session_factory):
        async for _ in svc.chat(messages, stream=False):
            pass

    assert sanitize_output, "sanitize 未产出"
    # payload 必须与 sanitize 输出完全一致（中间无任何额外变换）
    assert captured["payload"]["messages"] == sanitize_output, (
        "payload['messages'] 在 sanitize 之后被二次变换 — 疑似 K2 同族旁路"
    )
