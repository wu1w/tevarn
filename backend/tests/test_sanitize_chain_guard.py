"""Sanitize 调用链守护测试（防 K2 复发：重构/sync 静默旁路 _sanitize_messages_for_api）。

K2 教训：sanitize 层曾被一次重写静默覆盖丢失，导致 orphan tool pair 400 复发。
本测试不测 sanitize 的正确性（另有专门测试），而是守护「调用链不可旁路」：
每次 chat()/chat_complete() 发送给 API 的 payload，其 messages 必须是
_sanitize_messages_for_api 的返回值。

零 mock 原则：真实 threading HTTP 服务捕获线上请求体——
「wire 上的 payload 就是 sanitize 输出」是最强证据，无需任何 spy/替身。
"""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from backend.services.llm.openai_compatible import OpenAICompatibleService


class _CaptureHandler(BaseHTTPRequestHandler):
    """真实 HTTP 服务：捕获 POST body，返回合法 chat completion"""

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            self.server.captured_payload = json.loads(raw.decode("utf-8"))
        except Exception:
            self.server.captured_payload = None
        body = json.dumps({
            "choices": [{
                "message": {"role": "assistant", "content": "pong"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture()
def capture_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _CaptureHandler)
    srv.captured_payload = None
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture()
def svc(capture_server) -> OpenAICompatibleService:
    return OpenAICompatibleService(
        config=SimpleNamespace(
            base_url=f"http://127.0.0.1:{capture_server.server_port}",
            model="test-model",
            api_key="k",
            max_tokens=16,
            temperature=0.0,
        )
    )


_ORPHAN_MESSAGES = [
    {"role": "system", "content": "s"},
    {"role": "user", "content": "hi"},
    {"role": "tool", "content": "ORPHAN", "tool_call_id": "no-match"},  # orphan
    {"role": "assistant", "content": "answer"},
]


def test_wire_payload_is_sanitized(svc, capture_server):
    """发给 API 的真实 payload 必须剔除 orphan tool 消息（调用链未旁路）"""
    async def _run():
        async for _ in svc.chat(_ORPHAN_MESSAGES, stream=False):
            pass

    asyncio.run(_run())

    payload = capture_server.captured_payload
    assert payload, "HTTP 未发送（测试环境错误）"
    sent = payload["messages"]
    # orphan tool 被剔除后，不应有 content=='ORPHAN' 的 tool 消息
    assert all(m.get("content") != "ORPHAN" for m in sent)
    # 且正常消息仍在
    assert any(m.get("content") == "answer" for m in sent)


def test_chat_complete_also_passes_through_sanitize(svc, capture_server):
    """chat_complete 走 chat()，wire payload 同样必经 sanitize（守护非流式入口）"""
    async def _run():
        return await svc.chat_complete(_ORPHAN_MESSAGES)

    resp = asyncio.run(_run())
    assert resp.content == "pong"
    payload = capture_server.captured_payload
    assert payload, "chat_complete 未发出 HTTP"
    assert all(m.get("content") != "ORPHAN" for m in payload["messages"])


def test_sanitize_is_first_transformation_of_messages(svc, capture_server):
    """守护锚点：wire payload['messages'] 与 _sanitize_messages_for_api 输出逐字节一致。

    若未来有人在 sanitize 之后又插入一层未净化变换（K2 同族），此测试会失败。
    """
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "tool", "content": "ORPHAN", "tool_call_id": "x"},
    ]
    # 直接调用真实方法得到基准输出（非 spy：测试的是线上 payload 与该输出一致）
    expected = svc._sanitize_messages_for_api(messages)

    async def _run():
        async for _ in svc.chat(messages, stream=False):
            pass

    asyncio.run(_run())

    payload = capture_server.captured_payload
    assert payload, "HTTP 未发送"
    assert payload["messages"] == expected, (
        "payload['messages'] 在 sanitize 之后被二次变换 — 疑似 K2 同族旁路"
    )
