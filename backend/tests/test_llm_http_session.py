"""B1 修复验证：LLM HTTP 显式超时 + session 复用

- request_timeout/stream_timeout 读取 settings 且语义正确
- ensure_session 同 loop 复用 / 关闭后重建
- openai_compatible 非流式路径真实带上 timeout 参数（回归 300s 挂死病灶）
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import aiohttp
import pytest


def test_request_timeout_uses_settings():
    from backend.services.llm.http_session import request_timeout

    with patch("backend.services.llm.http_session.settings") as s:
        s.llm_request_timeout_seconds = 45.0
        s.llm_connect_timeout_seconds = 3.0
        t = request_timeout()
    assert t.total == 45.0
    assert t.connect == 3.0


def test_stream_timeout_no_total_but_sock_read():
    """流式不限总时长（长生成合法），但必须有停顿检测"""
    from backend.services.llm.http_session import stream_timeout

    with patch("backend.services.llm.http_session.settings") as s:
        s.llm_connect_timeout_seconds = 8.0
        s.llm_stream_read_timeout_seconds = 99.0
        t = stream_timeout()
    assert t.total is None
    assert t.connect == 8.0
    assert t.sock_read == 99.0


def test_default_timeouts_are_finite():
    """默认配置下非流式 total 必须有限（回归：此前 None=300s 挂死）"""
    from backend.services.llm.http_session import request_timeout

    t = request_timeout()
    assert t.total is not None and 10 <= t.total <= 600
    assert t.connect is not None and t.connect <= 30


def test_ensure_session_reuse_and_recreate():
    from backend.services.llm.http_session import ensure_session

    class _Svc:
        pass

    async def _run():
        svc = _Svc()
        s1 = ensure_session(svc)
        s2 = ensure_session(svc)
        assert s1 is s2  # 同 loop 复用
        await s1.close()
        s3 = ensure_session(svc)
        assert s3 is not s1  # 已关闭 → 重建
        assert not s3.closed
        await s3.close()

    asyncio.run(_run())


def test_openai_compatible_nonstream_passes_timeout():
    """非流式调用必须带显式 timeout（真实调用路径回归）"""
    from types import SimpleNamespace

    from backend.services.llm.openai_compatible import OpenAICompatibleService

    captured: dict = {}

    class _FakeResp:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def raise_for_status(self):
            pass

        async def json(self):
            return {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}

    class _FakeSession:
        closed = False

        def post(self, url, json=None, headers=None, timeout=None):
            captured["timeout"] = timeout
            return _FakeResp()

    svc = OpenAICompatibleService(
        config=SimpleNamespace(
            base_url="http://x", model="m", max_tokens=100,
            temperature=0.7, api_key="k",
        )
    )

    async def _run():
        # chat 内是函数级 import，patch 源模块即可生效
        with patch(
            "backend.services.llm.http_session.ensure_session",
            return_value=_FakeSession(),
        ):
            chunks = []
            async for ch in svc.chat([{"role": "user", "content": "hi"}], stream=False):
                chunks.append(ch)
            return chunks

    chunks = asyncio.run(_run())
    assert captured.get("timeout") is not None
    assert captured["timeout"].total is not None  # 非流式必须有总时限
    assert any(c.delta == "ok" for c in chunks)
