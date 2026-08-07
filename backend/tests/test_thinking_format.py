"""Unit tests for thinking / reasoning presentation helpers."""

from backend.agent.thinking_format import (
    is_visible_empty,
    strip_thinking,
    wrap_thinking,
)
from backend.agent.robust import is_empty_assistant_content


def test_wrap_thinking_with_body():
    out = wrap_thinking("step 1", "answer")
    assert out.startswith("<thinking>")
    assert "step 1" in out
    assert out.rstrip().endswith("answer")
    assert "</thinking>" in out


def test_wrap_thinking_empty_body():
    out = wrap_thinking("only reason", "")
    assert "<thinking>" in out
    assert "only reason" in out
    assert strip_thinking(out) == ""


def test_wrap_skips_double():
    already = "<thinking>\nx\n</thinking>\n\nbody"
    assert wrap_thinking("y", already) == already


def test_strip_and_empty():
    raw = wrap_thinking("hidden", "  visible  ")
    assert strip_thinking(raw) == "visible"
    assert not is_visible_empty(raw)
    assert is_visible_empty(wrap_thinking("only", ""))
    assert is_empty_assistant_content(wrap_thinking("only", ""))
    assert not is_empty_assistant_content(wrap_thinking("t", "hi"))
