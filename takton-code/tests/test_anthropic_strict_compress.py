"""Anthropic-strict multi-turn compress + microcompact + overflow detect."""

from __future__ import annotations

import tempfile
from pathlib import Path

from takton_code.context.compressor import (
    CLEARED_TOOL_RESULT,
    ContextCompressor,
    TokenMeter,
    ensure_anthropic_strict,
    estimate_messages,
    is_context_overflow_error,
    microcompact_tools,
    validate_tool_integrity,
)
from takton_code.llm.provider import _sanitize_messages


def _block(i: int, payload: str) -> list[dict]:
    cid = f"call_{i}"
    return [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": cid,
                    "type": "function",
                    "function": {"name": "file_read", "arguments": f'{{"path":"f{i}.py"}}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": cid, "name": "file_read", "content": payload},
        {"role": "assistant", "content": f"saw file {i}"},
        {"role": "user", "content": f"next {i}"},
    ]


def test_overflow_detector():
    assert is_context_overflow_error("Context length exceeded")
    assert is_context_overflow_error("prompt is too long for this model")
    assert is_context_overflow_error(RuntimeError("model_context_window_exceeded"))
    assert not is_context_overflow_error("permission denied")


def test_microcompact_keeps_pairs_and_clears_old():
    blob = "X" * 5000
    msgs: list[dict] = [{"role": "system", "content": "sys"}]
    for i in range(8):
        msgs.append({"role": "user", "content": f"u{i}"})
        msgs.extend(_block(i, blob))

    out, stats = microcompact_tools(msgs, keep_recent_blocks=2, max_tool_chars=800)
    assert validate_tool_integrity(out) == []
    assert stats["blocks_total"] >= 8
    # older tools cleared
    cleared = sum(
        1
        for m in out
        if m.get("role") == "tool"
        and isinstance(m.get("content"), str)
        and m["content"].startswith(CLEARED_TOOL_RESULT)
    )
    assert cleared >= 4
    # every assistant tool_calls still followed by tools
    assert validate_tool_integrity(out) == []


def test_long_multiturn_compress_never_breaks_anthropic():
    td = Path(tempfile.mkdtemp())
    meter = TokenMeter(context_window=8000, threshold_percent=0.25)
    c = ContextCompressor(
        meter=meter,
        keep_recent=6,
        keep_recent_tool_blocks=3,
        max_tool_chars=1000,
        offload_dir=td,
    )
    blob = ("TOOLDATA " * 200) + "\n"
    msgs: list[dict] = [{"role": "system", "content": "You are a coding agent."}]
    for turn in range(20):
        msgs.append({"role": "user", "content": f"task turn {turn} " + ("pad " * 40)})
        msgs.extend(_block(turn, blob * 3))
        # compress like agent loop each few turns
        if turn % 2 == 1:
            msgs = c.compress(msgs, force=False, reason="threshold")
            assert validate_tool_integrity(msgs) == [], validate_tool_integrity(msgs)
            msgs = ensure_anthropic_strict(msgs)
            assert validate_tool_integrity(msgs) == []

    # hard overflow path
    msgs = c.compress(msgs, force=True, reason="api_overflow", aggressive_tools=True)
    assert validate_tool_integrity(msgs) == []
    # sanitize path used by provider
    san = _sanitize_messages(msgs)
    assert validate_tool_integrity(san) == []
    # no orphan tools / incomplete blocks
    for i, m in enumerate(san):
        if m.get("role") == "assistant" and m.get("tool_calls"):
            ids = {tc["id"] for tc in m["tool_calls"]}
            j = i + 1
            got = set()
            while j < len(san) and san[j].get("role") == "tool":
                got.add(san[j]["tool_call_id"])
                j += 1
            assert ids <= got


def test_incomplete_block_stripped_not_sent():
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "x", "type": "function", "function": {"name": "grep", "arguments": "{}"}}
            ],
        },
        # missing tool result
        {"role": "user", "content": "what?"},
    ]
    out = ensure_anthropic_strict(msgs)
    assert validate_tool_integrity(out) == []
    assert not any(m.get("tool_calls") for m in out if m.get("role") == "assistant")


def test_broken_cut_middle_repaired():
    meter = TokenMeter(context_window=3000, threshold_percent=0.2)
    c = ContextCompressor(meter=meter, keep_recent=4, keep_recent_tool_blocks=2, max_tool_chars=500)
    blob = "Z" * 2000
    msgs: list[dict] = [{"role": "system", "content": "sys"}]
    for i in range(12):
        msgs.append({"role": "user", "content": f"u{i}{blob[:200]}"})
        msgs.extend(_block(i, blob))
    out = c.compress(msgs, force=True, reason="threshold")
    assert validate_tool_integrity(out) == []
    assert estimate_messages(out) < estimate_messages(msgs)
