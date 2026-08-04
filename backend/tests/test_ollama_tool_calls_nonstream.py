"""Ollama non-stream must surface tool_calls (parity with openai-compatible)."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services.llm.ollama import OllamaService


class _Resp:
    def __init__(self, payload: dict):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def raise_for_status(self):
        return None

    async def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_ollama_nonstream_emits_tool_calls():
    svc = OllamaService(
        SimpleNamespace(base_url="http://127.0.0.1:11434", model="qwen", max_tokens=256, temperature=0.1)
    )
    payload = {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "function": {
                        "name": "file_read",
                        "arguments": {"path": "calc.py"},
                    }
                }
            ],
        }
    }
    session = MagicMock()
    session.post = MagicMock(return_value=_Resp(payload))

    with patch("backend.services.llm.ollama.ensure_session", return_value=session):
        chunks = []
        async for c in svc.chat([{"role": "user", "content": "read"}], tools=[{"type":"function","function":{"name":"file_read","parameters":{}}}], stream=False):
            chunks.append(c)

    tcs = [c.tool_call for c in chunks if c.tool_call]
    assert len(tcs) == 1
    assert tcs[0].name == "file_read"
    assert tcs[0].arguments.get("path") == "calc.py"
    assert any(c.finish_reason in ("tool_calls", "stop") for c in chunks)
