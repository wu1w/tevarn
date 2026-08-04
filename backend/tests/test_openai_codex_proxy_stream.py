"""Codex OAuth proxy stream hardening (Luna mid-run crash)."""

from __future__ import annotations

from backend.api.routes.openai_codex_proxy import (
    _codex_upstream_timeout,
    _flush_sse_data_buffer,
    _iter_sse_data_lines,
)


def test_codex_timeout_no_total_and_floor_sock_read():
    t = _codex_upstream_timeout()
    assert t.total is None
    assert t.sock_read is not None and t.sock_read >= 300.0
    assert t.connect is not None and t.connect > 0


def test_iter_sse_buffers_half_lines():
    buf = ""
    buf, lines = _iter_sse_data_lines(buf, b'data: {"type":"response.output_text.delta","delta":"hel')
    assert lines == []
    assert buf.startswith("data:")
    buf, lines = _iter_sse_data_lines(buf, b'lo"}\n')
    assert lines == ['{"type":"response.output_text.delta","delta":"hello"}']
    assert buf == ""


def test_iter_sse_handles_crlf_and_done():
    buf = ""
    buf, lines = _iter_sse_data_lines(
        buf, b"data: {\"a\":1}\r\ndata: [DONE]\r\npartial"
    )
    assert lines == ['{"a":1}', "[DONE]"]
    assert buf == "partial"


def test_iter_sse_skips_non_data():
    buf, lines = _iter_sse_data_lines("", b": keepalive\nevent: ping\ndata: {\"x\":1}\n")
    assert lines == ['{"x":1}']


def test_flush_sse_residual_without_trailing_newline():
    """Providers often omit final \\n; residual must still parse."""
    buf = ""
    buf, lines = _iter_sse_data_lines(buf, b'data: {"type":"response.completed","response":{}}')
    assert lines == []
    assert "response.completed" in buf
    flushed = _flush_sse_data_buffer(buf)
    assert flushed == ['{"type":"response.completed","response":{}}']
    assert _flush_sse_data_buffer("") == []
    assert _flush_sse_data_buffer("   ") == []
