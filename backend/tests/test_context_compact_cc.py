"""Claude Code–aligned compaction semantics.

Regression: L5 inject must CONTINUE work (not ban resume);
mid-loop compress must not run L5; L3 preserves tool pairs.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.agent.context_pipeline import (
    CLEARED_TOOL_PLACEHOLDER,
    SUMMARY_CONTINUE,
    SUMMARY_PREFIX,
    PipelineContextEngine,
    build_compact_continuation_message,
    format_compact_summary,
)


def test_summary_prefix_is_continuation_not_ban():
    """旧版「REFERENCE ONLY / Do not resume」不得再出现。"""
    bad_phrases = [
        "REFERENCE ONLY",
        "NOT active instructions",
        "Respond ONLY to the latest user message",
        "Do not resume Historical Remaining Work",
    ]
    blob = SUMMARY_PREFIX + "\n" + SUMMARY_CONTINUE
    for p in bad_phrases:
        assert p not in blob, f"banned phrase still present: {p}"
    assert "continued" in SUMMARY_PREFIX.lower() or "继续" in SUMMARY_PREFIX
    assert "Pick up the last task" in SUMMARY_CONTINUE or "Resume" in SUMMARY_CONTINUE


def test_build_compact_continuation_message_shape():
    body = build_compact_continuation_message(
        "1. Primary Request\nfix the bug",
        recent_messages_preserved=True,
    )
    assert SUMMARY_PREFIX in body
    assert SUMMARY_CONTINUE in body
    assert "fix the bug" in body
    assert "Recent messages" in body
    for p in ("REFERENCE ONLY", "Do not resume Historical"):
        assert p not in body


def test_format_compact_summary_strips_analysis():
    raw = (
        "<analysis>thinking privately</analysis>\n"
        "<summary>\n1. Primary Request and Intent:\n   ship feature\n</summary>"
    )
    out = format_compact_summary(raw)
    assert "thinking privately" not in out
    assert "ship feature" in out
    assert "Summary:" in out


@pytest.mark.asyncio
async def test_l5_injects_user_continuation_not_system_ban(monkeypatch):
    eng = PipelineContextEngine()
    eng.protect_last_n = 6

    async def _fake(self, transcript, focus_line):
        return (
            "<analysis>x</analysis><summary>\n"
            "1. Primary Request and Intent:\n   修登录 bug\n"
            "7. Pending Tasks:\n   改 auth.py\n"
            "8. Current Work:\n   读完 auth.py\n"
            "9. Optional Next Step:\n   调用 edit 修 off-by-one\n"
            "</summary>"
        )

    monkeypatch.setattr(PipelineContextEngine, "_llm_summarize", _fake)

    messages = [{"role": "system", "content": "You are Takton"}]
    for i in range(5):
        messages.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"c{i}",
                        "type": "function",
                        "function": {"name": "file_read", "arguments": "{}"},
                    }
                ],
            }
        )
        messages.append(
            {"role": "tool", "tool_call_id": f"c{i}", "content": f"result {i} " + "Z" * 50}
        )
    messages.append({"role": "user", "content": "继续修"})
    for j in range(5):
        messages.append({"role": "user", "content": f"tail{j}"})

    out, meta = await eng._l5_auto_compact(
        messages, focus_topic=None, session_id=None
    )
    assert meta.get("applied")
    assert meta.get("continuation") is True

    # summary is user-role continuation
    compact_msgs = [m for m in out if m.get("_compressed_summary")]
    assert len(compact_msgs) == 1
    assert compact_msgs[0]["role"] == "user"
    content = compact_msgs[0]["content"]
    assert "Do not resume Historical" not in content
    assert "REFERENCE ONLY" not in content
    assert "Pick up the last task" in content or "continued" in content.lower()
    assert "修登录" in content or "Primary" in content or "auth" in content


@pytest.mark.asyncio
async def test_compress_micro_only_skips_l5():
    eng = PipelineContextEngine()
    eng.enable_l5 = True
    l5 = AsyncMock(return_value=([], {"applied": True}))
    with patch.object(eng, "_l5_auto_compact", new=l5):
        eng.meter.should_compress = lambda *a, **k: True  # type: ignore
        msgs = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u"},
            {"role": "assistant", "content": "a"},
            {"role": "tool", "content": "t" * 500, "tool_call_id": "1"},
        ]
        _, meta = await eng.compress(msgs, current_tokens=99_000, allow_l5=False)
    assert l5.await_count == 0
    assert meta.get("l5_skipped_midloop") is True
    assert "L5" not in (meta.get("layers") or [])


@pytest.mark.asyncio
async def test_compress_allow_l5_can_run():
    eng = PipelineContextEngine()
    l5 = AsyncMock(
        return_value=(
            [{"role": "system", "content": "s"}, {"role": "user", "content": "ok"}],
            {"applied": True, "continuation": True},
        )
    )
    with patch.object(eng, "_l5_auto_compact", new=l5):
        eng.meter.should_compress = lambda *a, **k: True  # type: ignore
        msgs = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
        ]
        _, meta = await eng.compress(msgs, current_tokens=99_000, allow_l5=True)
    assert l5.await_count == 1
    assert "L5" in (meta.get("layers") or [])


def test_l3_placeholder_constant():
    assert "cleared" in CLEARED_TOOL_PLACEHOLDER.lower()
