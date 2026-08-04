# -*- coding: utf-8 -*-
"""Codex SSE isolation: child worker + parent parser (no live network)."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from backend.services.llm.codex_sse_isolate import (
    consume_sse_bytes_to_events,
    isolate_enabled,
)


def test_isolate_default_on_windows():
    prev = os.environ.pop("TAKTON_CODEX_SSE_ISOLATE", None)
    try:
        if sys.platform == "win32":
            assert isolate_enabled() is True
        os.environ["TAKTON_CODEX_SSE_ISOLATE"] = "0"
        assert isolate_enabled() is False
        os.environ["TAKTON_CODEX_SSE_ISOLATE"] = "1"
        assert isolate_enabled() is True
    finally:
        if prev is None:
            os.environ.pop("TAKTON_CODEX_SSE_ISOLATE", None)
        else:
            os.environ["TAKTON_CODEX_SSE_ISOLATE"] = prev


@pytest.mark.asyncio
async def test_consume_sse_bytes_to_events():
    async def gen():
        payload = (
            b'data: {"type":"response.output_text.delta","delta":"hi"}\n\n'
            b"data: [DONE]\n\n"
        )
        yield payload

    items = []
    async for x in consume_sse_bytes_to_events(gen()):
        items.append(x)
    assert items[0]["type"] == "response.output_text.delta"
    assert items[0]["delta"] == "hi"
    assert items[-1] == "[DONE]"


def test_worker_module_importable():
    # Ensure -m backend.services.llm.codex_sse_worker resolves
    import backend.services.llm.codex_sse_worker as w

    assert callable(w.main)


def test_converter_as_chat_completion():
    from backend.api.routes.openai_codex_proxy import _CodexStreamToChat

    c = _CodexStreamToChat("gpt-5.6-luna")
    c.feed(
        {
            "type": "response.output_text.delta",
            "delta": "hello ",
        }
    )
    c.feed(
        {
            "type": "response.output_item.done",
            "item": {
                "type": "function_call",
                "id": "fc1",
                "call_id": "call_1",
                "name": "file_read",
                "arguments": '{"path":"a.py"}',
            },
        }
    )
    c.feed({"type": "response.completed", "response": {"id": "resp_x", "usage": {}}})
    out = c.as_chat_completion()
    assert out["choices"][0]["message"]["content"] == "hello "
    tcs = out["choices"][0]["message"]["tool_calls"]
    assert len(tcs) == 1
    assert tcs[0]["function"]["name"] == "file_read"
