"""P0 harness fixes: timeout classification, history normalize, L3 partial clear."""

from __future__ import annotations

from backend.agent.history_normalize import normalize_history_for_llm
from backend.agent.tool_result_contract import is_tool_error


def test_is_tool_error_accepts_timeout_forms():
    assert is_tool_error("[Error] Tool 'command' timed out after 180s")
    assert is_tool_error("[Timeout] Execution exceeded 30s")
    assert is_tool_error("[Timeout] command exceeded 90s and was terminated")
    assert is_tool_error("[Error] boom")
    assert not is_tool_error("[Success] written ok")
    assert not is_tool_error("hello world")
    assert not is_tool_error(
        "[Background after timeout] id=bg_abc\nUse process poll"
    )


def test_chat_dedup_window():
    from backend.api.chat_dedup import should_drop_duplicate_user

    sid = "sess-dedup-test"
    assert should_drop_duplicate_user(sid, "hello world") is False
    assert should_drop_duplicate_user(sid, "hello world") is True
    assert should_drop_duplicate_user(sid, "different") is False


def test_auto_remember_signal():
    from backend.agent.auto_remember import _should_remember

    assert _should_remember(
        "技术栈用 hamvor，先写 PRD",
        "好的，我们确定技术栈为 … 并写入 PRD。",
    )
    assert not _should_remember("ok", "嗯")


def test_normalize_synthesizes_missing_tool_results():
    msgs = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "command", "arguments": "{}"},
                },
                {
                    "id": "call-2",
                    "type": "function",
                    "function": {"name": "file_read", "arguments": "{}"},
                },
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "name": "command",
            "content": "ok",
        },
        # orphan — no matching assistant call
        {
            "role": "tool",
            "tool_call_id": "call-ghost",
            "name": "x",
            "content": "orphan body",
        },
    ]
    out = normalize_history_for_llm(msgs)
    ids = [
        m.get("tool_call_id")
        for m in out
        if m.get("role") == "tool"
    ]
    assert "call-ghost" not in ids
    assert "call-1" in ids
    assert "call-2" in ids
    synth = [m for m in out if m.get("tool_call_id") == "call-2"][0]
    assert "[aborted]" in (synth.get("content") or "")


def test_l3_clears_short_tools_without_requiring_three():
    from backend.agent.context_pipeline import PipelineContextEngine

    eng = PipelineContextEngine()
    # Build: system + protect head + mid tools + protect tail
    msgs = [{"role": "system", "content": "sys"}]
    for i in range(3):
        msgs.append({"role": "user", "content": f"u{i}"})
    # mid tools with short-but-clearable bodies
    for i in range(5):
        msgs.append(
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
        msgs.append(
            {
                "role": "tool",
                "tool_call_id": f"c{i}",
                "name": "file_read",
                "content": ("x" * 80) + f" body{i}",
            }
        )
    for i in range(12):
        msgs.append({"role": "user", "content": f"tail{i}"})

    out, n = eng._l3_microcompact(msgs)
    assert n >= 1, f"expected L3 to clear some tools, got n={n}"
