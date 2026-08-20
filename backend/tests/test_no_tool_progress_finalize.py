"""S8: progress notes after tools must not finalize (once)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.agent.phases.no_tool_round import run_no_tool_round
from backend.agent.turn_retry import TurnRetryState
from backend.agent.progress_guard import unread_result_handles
from backend.agent.user_channel import looks_like_complete_final_answer, user_visible_content

_PROGRESS = "官方仓库正文再核一下核心循环和许可，避免只凭摘要下结论。\n<|eos|>"
_HANDLE_ID = "89a98908abcdef0123456789abcdef01"
_HANDLE_MSG = (
    f"[tool_result_handle id={_HANDLE_ID} bytes=12000 spilled=1] "
    "use result_load to page this extract."
)
_COMPLETE = (
    "## 官方仓库\n\n"
    "- Qwen 有公开的 agent 仓库，核心循环是 observe → tool → observe。\n"
    "- 许可证是 Apache-2.0，允许复制核心 loop 到本地 harness。\n"
    "- 需要自己接模型与工具协议，没有现成的一键本地 agent 发行版。\n"
    "- 搜索/extract 结果已外置，结论以正文仓库说明为准，不凭摘要。\n\n"
    "## 结论\n\n"
    "可以复制核心循环：读取用户输入、选工具、执行、把结果写回上下文，再决定是否继续。"
    "官方仓库正文写明了许可与入口文件，本地实现时保持同样的 turn 结构即可。"
    "这不是进度说明，而是对本问题的完整答复，含仓库、许可、以及可复用的循环步骤。"
    "建议先读 README 与 agent loop 文件，再按自己的工具面裁剪，而不是再搜一遍。"
    "以上已经足够作为本轮对用户的完整答复，无需再停在一句进度上。"
    "下一步只在用户追问时再开工具，而不是自动再搜一遍摘要。\n"
)


def _loop(**extra):
    ns = SimpleNamespace(
        _should_stop=False,
        last_exit_reason="",
        _push_status=AsyncMock(),
        model="test",
        _model_name="test",
    )
    for k, v in extra.items():
        setattr(ns, k, v)
    return ns


def _tool_messages():
    return [
        {
            "role": "tool",
            "name": "tavily_search",
            "content": "hits about qwen agent harness",
        },
        {
            "role": "tool",
            "name": "tavily_extract",
            "content": _HANDLE_MSG,
        },
    ]


async def _run(loop, *, content, messages, last_tool_round_count, tools_used_run):
    return await run_no_tool_round(
        loop,
        session_id=uuid.uuid4(),
        iteration=3,
        seg_size=40,
        messages=messages,
        accumulated_content=content,
        accumulated_reasoning="",
        goal_mode=False,
        goal_nudge_count=0,
        turn_retry=TurnRetryState(),
        empty_reply_max=2,
        last_tool_round_count=last_tool_round_count,
        force_final_no_tools=False,
        user_input="Qwen official local agent harness?",
        enriched_input="Qwen official local agent harness?",
        tools_used_run=tools_used_run,
        completion_followups=0,
    )


@pytest.mark.asyncio
async def test_progress_note_after_tools_and_handle_continues():
    loop = _loop()
    messages = _tool_messages()
    result = await _run(
        loop,
        content=_PROGRESS,
        messages=messages,
        last_tool_round_count=2,
        tools_used_run=["tavily_search", "tavily_extract"],
    )
    assert result.action == "continue"
    assert result.force_final_no_tools is False
    assert loop._progress_note_nudge is True
    if result.final_content is not None:
        assert "<|eos|>" not in result.final_content
    assert any(
        isinstance(m, dict)
        and m.get("role") == "system"
        and "result_load" in str(m.get("content") or "")
        and _HANDLE_ID in str(m.get("content") or "")
        for m in messages
    )


@pytest.mark.asyncio
async def test_already_nudged_unread_handle_still_continues():
    """Unread extract handle must not finalize on the second progress note."""
    loop = _loop(_progress_note_nudge=True)
    messages = _tool_messages()
    result = await _run(
        loop,
        content=_PROGRESS,
        messages=messages,
        last_tool_round_count=2,
        tools_used_run=["tavily_search", "tavily_extract"],
    )
    assert result.action == "continue"
    assert result.force_final_no_tools is False
    assert int(getattr(loop, "_unread_handle_nudge", 0) or 0) == 1
    assert any(
        isinstance(m, dict)
        and m.get("role") == "system"
        and _HANDLE_ID in str(m.get("content") or "")
        for m in messages
    )


@pytest.mark.asyncio
async def test_progress_note_breaks_after_handle_is_loaded():
    loop = _loop(_progress_note_nudge=True)
    messages = _tool_messages() + [
        {
            "role": "tool",
            "name": "result_load",
            "content": (
                f"[result_load id={_HANDLE_ID} offset=0 end=12000 total=12000]\n"
                "full extract body already paged in"
            ),
        }
    ]
    result = await _run(
        loop,
        content=_PROGRESS,
        messages=messages,
        last_tool_round_count=3,
        tools_used_run=["tavily_search", "tavily_extract", "result_load"],
    )
    assert result.action == "break"
    assert getattr(loop, "last_exit_reason", "") == "non_goal_text_final"
    assert result.final_content is not None
    assert "<|eos|>" not in result.final_content
    assert "核心循环" in (result.final_content or "")


@pytest.mark.asyncio
async def test_unread_handle_cap_asks_for_final_without_tools():
    loop = _loop(_progress_note_nudge=True, _unread_handle_nudge=3)
    messages = _tool_messages()
    result = await _run(
        loop,
        content=_PROGRESS,
        messages=messages,
        last_tool_round_count=2,
        tools_used_run=["tavily_search", "tavily_extract"],
    )
    assert result.action == "continue"
    assert result.force_final_no_tools is True
    assert int(loop._unread_handle_nudge) == 4


@pytest.mark.asyncio
async def test_complete_markdown_after_tools_still_breaks():
    loop = _loop()
    assert looks_like_complete_final_answer(_COMPLETE)
    result = await _run(
        loop,
        content=_COMPLETE,
        messages=_tool_messages(),
        last_tool_round_count=2,
        tools_used_run=["tavily_search", "tavily_extract"],
    )
    assert result.action == "break"
    assert getattr(loop, "last_exit_reason", "") == "non_goal_text_final"
    assert getattr(loop, "_progress_note_nudge", False) is False


@pytest.mark.asyncio
async def test_progress_note_without_tools_still_breaks():
    loop = _loop()
    result = await _run(
        loop,
        content="正在读取仓库关键文件…",
        messages=[],
        last_tool_round_count=0,
        tools_used_run=[],
    )
    assert result.action == "break"
    assert getattr(loop, "last_exit_reason", "") == "non_goal_text_final"
    assert getattr(loop, "_progress_note_nudge", False) is False


def test_user_visible_strips_eos_token():
    out = user_visible_content(_PROGRESS)
    assert "<|eos|>" not in out
    assert "核心循环" in out


def test_unread_result_handles_ignores_loaded_ids():
    msgs = _tool_messages() + [
        {
            "role": "tool",
            "name": "result_load",
            "content": f"[result_load id={_HANDLE_ID} offset=0 end=10 total=10]\nbody",
        }
    ]
    assert unread_result_handles(_tool_messages()) == [_HANDLE_ID]
    assert unread_result_handles(msgs) == []

_RESULT_LOAD_JSON = (
    "仓库正文已经拉到了，先分页读核心 README，再给你能不能照搬的结论。\n"
    "{\n"
    ' "name": "result_load",\n'
    ' "arguments": {\n'
    ' "id": "04be9012cc464e4c",\n'
    ' "offset": 8000,\n'
    ' "max_chars": 16000\n'
    " }\n"
    "}\n"
)


@pytest.mark.asyncio
async def test_s8_does_not_finalize_result_load_json_after_tools_and_handle():
    loop = _loop()
    messages = _tool_messages()
    result = await _run(
        loop,
        content=_RESULT_LOAD_JSON,
        messages=messages,
        last_tool_round_count=2,
        tools_used_run=["tavily_search", "tavily_extract"],
    )
    assert result.action == "continue"
    if result.final_content is not None:
        assert "result_load" not in result.final_content
        assert "<|eos|>" not in result.final_content


@pytest.mark.asyncio
async def test_second_pseudo_leak_stops_without_another_llm_round():
    loop = _loop(_pseudo_tool_leak_streak=1)
    result = await _run(
        loop,
        content=_RESULT_LOAD_JSON,
        messages=_tool_messages(),
        last_tool_round_count=2,
        tools_used_run=["tavily_search", "tavily_extract"],
    )
    assert result.action == "break"
    assert result.final_content
    assert "result_load" not in result.final_content
    assert "tool_call" not in (result.final_content or "")

