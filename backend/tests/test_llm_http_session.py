"""B1 修复验证：LLM HTTP 显式超时 + session 复用

零 mock 原则：全部真实组件——
- 真实 HTTP 服务（threading http.server，可控延迟/响应）
- 真实 aiohttp client 经 openai_compatible 真实调用
- 真实 settings 属性 set/restore（不用 patch/MagicMock）
- 真实 sqlite 不需要（本测试无 DB）
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from backend.core.config import settings

# ─────────── 真实可控 HTTP 服务 ───────────


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        delay = getattr(self.server, "delay", 0.0)
        if delay:
            time.sleep(delay)
        body = json.dumps({
            "choices": [{
                "message": {"content": "pong"},
                "finish_reason": "stop",
            }]
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 静音
        pass


@pytest.fixture()
def http_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.delay = 0.0
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture()
def svc(http_server):
    from backend.services.llm.openai_compatible import OpenAICompatibleService

    return OpenAICompatibleService(
        config=SimpleNamespace(
            base_url=f"http://127.0.0.1:{http_server.server_port}",
            model="m",
            max_tokens=100,
            temperature=0.7,
            api_key="k",
        )
    )


@pytest.fixture()
def fast_timeout():
    """真实 settings 属性 set/restore（非 mock）"""
    old = (
        settings.llm_request_timeout_seconds,
        settings.llm_connect_timeout_seconds,
    )
    settings.llm_request_timeout_seconds = 0.5
    settings.llm_connect_timeout_seconds = 0.5
    yield
    settings.llm_request_timeout_seconds, settings.llm_connect_timeout_seconds = old


# ─────────── 超时行为（真实网络） ───────────


def test_slow_provider_times_out_fast(svc, http_server, fast_timeout):
    """provider 卡顿（2s 才响应）+ total=0.5s → 必须快速失败，不挂 300s（B1 回归）

    失败契约：非流式路径把网络异常吞成 finish_reason=error 的 chunk（既有行为），
    本测试断言的是「快速」——0.5s 超时生效，而不是挂到 300s。
    """
    http_server.delay = 2.0

    async def _run():
        t0 = time.perf_counter()
        chunks = []
        async for ch in svc.chat([{"role": "user", "content": "hi"}], stream=False):
            chunks.append(ch)
        return time.perf_counter() - t0, chunks

    elapsed, chunks = asyncio.run(_run())
    # 0.5s 超时 + 容忍调度抖动；远小于病灶期的 300s
    assert elapsed < 1.5, f"超时未生效：{elapsed:.2f}s 才失败"
    assert any(c.finish_reason == "error" for c in chunks), (
        f"超时应以 error chunk 暴露，实际: {[(c.delta, c.finish_reason) for c in chunks]}"
    )


def test_fast_provider_roundtrip(svc):
    """正常 provider：真实 HTTP 往返拿到内容（证明改造没破坏正常路径）"""
    async def _run():
        chunks = []
        async for ch in svc.chat([{"role": "user", "content": "hi"}], stream=False):
            chunks.append(ch)
        return chunks

    chunks = asyncio.run(_run())
    assert any(c.delta == "pong" for c in chunks)


# ─────────── 超时配置语义（真实 settings） ───────────


def test_request_timeout_reads_settings(fast_timeout):
    from backend.services.llm.http_session import request_timeout

    t = request_timeout()
    assert t.total == 0.5
    assert t.connect == 0.5


def test_stream_timeout_no_total_but_sock_read():
    """流式不限总时长（长生成合法），但必须有停顿检测"""
    from backend.services.llm.http_session import stream_timeout

    old = settings.llm_stream_read_timeout_seconds
    settings.llm_stream_read_timeout_seconds = 99.0
    try:
        t = stream_timeout()
    finally:
        settings.llm_stream_read_timeout_seconds = old
    assert t.total is None
    assert t.sock_read == 99.0


def test_default_timeouts_are_finite():
    """默认配置下非流式 total 必须有限（回归：此前 None→aiohttp 300s 挂死）"""
    from backend.services.llm.http_session import request_timeout

    t = request_timeout()
    assert t.total is not None and 10 <= t.total <= 600
    assert t.connect is not None and t.connect <= 30


# ─────────── session 复用（真实 aiohttp session） ───────────


def test_ensure_session_reuse_and_recreate():
    from backend.services.llm.http_session import ensure_session

    class _Svc:
        pass

    async def _run():
        svc = _Svc()
        s1 = ensure_session(svc)
        s2 = ensure_session(svc)
        assert s1 is s2  # 同 loop 复用（连接池生效）
        await s1.close()
        s3 = ensure_session(svc)
        assert s3 is not s1  # 已关闭 → 重建
        assert not s3.closed
        await s3.close()

    asyncio.run(_run())
