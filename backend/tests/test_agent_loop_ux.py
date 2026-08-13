"""Agent-loop UX: thinking off user channel, greetings vs repo tool-first, wrap-up."""

from __future__ import annotations

import asyncio
import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ["TEVARN_LLM_ALLOW_PY_FALLBACK"] = "1"
try:
    from backend.kernel.llm_admission import reset_llm_admission_for_tests

    reset_llm_admission_for_tests()
except Exception:
    pass

from backend.tests.test_loop_freeze import _make_loop as _make_loop_raw
from backend.tests.test_loop_freeze import _run_loop, _ScriptedLLM
from backend.tests.test_user_channel import _REVIEW


def _chunk(delta=None, tool_call=None, finish=None, reasoning_delta=None):
    return SimpleNamespace(
        delta=delta,
        tool_call=tool_call,
        finish_reason=finish,
        reasoning_delta=reasoning_delta,
        usage=None,
    )


class _ScriptedLLMRespectTools(_ScriptedLLM):
    """When the loop passes tools=None (force_final), stop emitting tool_calls."""

    async def chat(self, messages, tools=None, stream=True):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools})
        if not tools:
            yield _chunk(delta="审查结论：到此收束。", finish="stop")
            return
        chunks = (
            self.script.pop(0)
            if self.script
            else [_chunk(delta="(剧本耗尽)", finish="stop")]
        )
        for ch in chunks:
            yield ch


def _make_loop(llm):
    loop, patcher = _make_loop_raw(llm)

    class _Admit:
        async def acquire(self, req=None):
            return SimpleNamespace(identity_id=None)

        async def release(self, lease=None):
            return None

    p_adm = patch(
        "backend.kernel.llm_scheduler.get_llm_admission",
        return_value=_Admit(),
    )
    p_adm.start()
    loop._ux_admit_patcher = p_adm  # type: ignore[attr-defined]
    return loop, patcher


def _stop_loop(loop, patcher, *extra):
    adm = getattr(loop, "_ux_admit_patcher", None)
    if adm is not None:
        try:
            adm.stop()
        except Exception:
            pass
    for p in extra:
        try:
            p.stop()
        except Exception:
            pass
    patcher.stop()


def _tc(tc_id, name, arguments, extra_content=None, thought_signature=None):
    return SimpleNamespace(
        id=tc_id,
        name=name,
        arguments=arguments,
        extra_content=extra_content,
        thought_signature=thought_signature,
        type="function",
    )


def _saved_assistant_texts(loop) -> list[str]:
    out: list[str] = []
    for c in loop._save_message.call_args_list:
        args, kwargs = c
        # save_message(session_id, role, content, ...)
        if len(args) >= 3 and args[1] == "assistant":
            out.append(str(args[2] or ""))
        elif kwargs.get("role") == "assistant":
            out.append(str(kwargs.get("content") or ""))
    for c in loop._persist_final_response.call_args_list:
        args, kwargs = c
        if len(args) >= 2:
            out.append(str(args[1] or ""))
        elif "final_content" in kwargs:
            out.append(str(kwargs["final_content"] or ""))
    return out


def _streamed_text(loop) -> str:
    parts: list[str] = []
    for c in loop._push_stream.call_args_list:
        args = c.args
        # _push_stream(session_id, message_id, delta)
        if len(args) >= 3:
            parts.append(str(args[2] or ""))
        elif c.kwargs.get("delta"):
            parts.append(str(c.kwargs["delta"]))
    return "".join(parts)


def test_greeting_answers_without_tools_and_strips_thinking():
    """在吗 → 直接作答；thinking 不得进入 persist / 用户 stream。"""
    llm = _ScriptedLLM(
        [
            [
                _chunk(reasoning_delta="user said hi, reply warmly"),
                _chunk(delta="在的"),
                _chunk(delta="，有什么事？", finish="stop"),
            ]
        ]
    )
    loop, patcher = _make_loop(llm)
    try:
        out = asyncio.run(_run_loop(loop, uuid.uuid4(), text="在吗"))
    finally:
        _stop_loop(loop, patcher)

    assert "在的" in out
    assert len(llm.calls) == 1
    joined = "\n".join(_saved_assistant_texts(loop))
    assert "<thinking>" not in joined
    assert "reply warmly" not in joined
    streamed = _streamed_text(loop)
    assert "<thinking>" not in streamed
    assert "reply warmly" not in streamed
    assert "在的" in streamed or "在的" in joined


def test_review_repo_does_not_persist_pretool_fabrication():
    """审查仓库：首轮编造简介 + tool 不得落成用户气泡。"""
    fab = (
        "Tevarn 是一个用 Rust 写的 SOCKS5 HTTP 代理，主要提供流量转发与隧道。"
        "下面我先讲架构再看代码。"
    )
    sig = {"google": {"thought_signature": "SIG-KEEP"}}
    llm = _ScriptedLLM(
        [
            [
                _chunk(delta=fab),
                _chunk(
                    tool_call=_tc(
                        "tc1",
                        "file_read",
                        {"path": "README.md"},
                        extra_content=sig,
                        thought_signature="SIG-KEEP",
                    ),
                    finish="tool_calls",
                ),
            ],
            [_chunk(delta="读过 README：这是 Tevarn 助手，不是 SOCKS5 代理。", finish="stop")],
        ]
    )
    loop, patcher = _make_loop(llm)
    loop._execute_registered_tool = AsyncMock(return_value="README: Tevarn agent")
    loop.ws_manager = SimpleNamespace(broadcast=AsyncMock())
    registry_patcher = patch(
        "backend.tools.registry.ToolRegistry.get",
        return_value=SimpleNamespace(parameters={"type": "object", "properties": {}}),
    )
    registry_patcher.start()
    try:
        sid = uuid.uuid4()
        out = asyncio.run(
            _run_loop(
                loop,
                sid,
                text="帮我审查 https://github.com/wu1w/tevarn 的逻辑 bug",
            )
        )
    finally:
        registry_patcher.stop()
        _stop_loop(loop, patcher)

    assert "Rust 写的 SOCKS5" not in (out or "")
    saved = "\n".join(_saved_assistant_texts(loop))
    assert "Rust 写的 SOCKS5" not in saved
    assert "流量转发" not in saved
    assert loop._execute_registered_tool.called
    # Gemini signature still on the next LLM turn
    assert len(llm.calls) >= 2
    asst = [
        m
        for m in llm.calls[1]["messages"]
        if m.get("role") == "assistant" and m.get("tool_calls")
    ]
    assert asst, "tool-call turn must be in next LLM messages"
    shaped = asst[0]["tool_calls"][0]
    assert shaped.get("thought_signature") == "SIG-KEEP"
    assert (shaped.get("extra_content") or {}).get("google", {}).get(
        "thought_signature"
    ) == "SIG-KEEP"
    # Stream retract
    resets = [
        c.args[1]
        for c in loop.ws_manager.broadcast.call_args_list
        if len(c.args) >= 2 and isinstance(c.args[1], dict)
        and c.args[1].get("type") == "content_reset"
    ]
    assert resets, "pre-tool essay must be retracted from the user stream"
    assert all("流量转发" not in str(r.get("content") or "") for r in resets)


def test_wrapup_dedup_stops_second_complete_review():
    """完整审查稿已写出后再调工具重写 → 停，不再额外 LLM 轮。"""
    llm = _ScriptedLLM(
        [
            [_chunk(tool_call=_tc("t1", "file_read", {"path": "a.py"}), finish="tool_calls")],
            [
                _chunk(delta=_REVIEW),
                _chunk(tool_call=_tc("t2", "glob", {"pattern": "**/*.py"}), finish="tool_calls"),
            ],
            [
                _chunk(delta=_REVIEW + "\n\n（补充一条同类结论。）"),
                _chunk(tool_call=_tc("t3", "glob", {"pattern": "**/*.rs"}), finish="tool_calls"),
            ],
            [_chunk(delta="不应再来第四轮", finish="stop")],
        ]
    )
    loop, patcher = _make_loop(llm)
    loop.max_iterations = 8
    loop._execute_registered_tool = AsyncMock(return_value="ok")
    registry_patcher = patch(
        "backend.tools.registry.ToolRegistry.get",
        return_value=SimpleNamespace(parameters={"type": "object", "properties": {}}),
    )
    registry_patcher.start()
    try:
        out = asyncio.run(
            _run_loop(loop, uuid.uuid4(), text="帮我审查 https://github.com/wu1w/tevarn")
        )
    finally:
        registry_patcher.stop()
        _stop_loop(loop, patcher)

    assert "不应再来第四轮" not in (out or "")
    assert "逻辑问题" in (out or "")
    assert len(llm.calls) == 3  # fourth scripted round never sampled


def test_auto_continue_segment_does_not_treat_reasoning_as_recorder():
    """6-iter (here 2-iter) segment: reasoning str must not abort auto-continue."""
    from backend.core.config import settings as _st

    llm = _ScriptedLLM(
        [
            [
                _chunk(reasoning_delta="planning reads"),
                _chunk(
                    tool_call=_tc("t1", "file_read", {"path": "a.py"}),
                    finish="tool_calls",
                ),
            ],
            [
                _chunk(reasoning_delta="more plan"),
                _chunk(
                    tool_call=_tc("t2", "glob", {"pattern": "*.py"}),
                    finish="tool_calls",
                ),
            ],
            [_chunk(delta="续跑后的答复", finish="stop")],
        ]
    )
    loop, patcher = _make_loop(llm)
    loop.max_iterations = 2
    loop._execute_registered_tool = AsyncMock(return_value="ok")
    registry_patcher = patch(
        "backend.tools.registry.ToolRegistry.get",
        return_value=SimpleNamespace(parameters={"type": "object", "properties": {}}),
    )
    ck = patch(
        "backend.agent.checkpoint.save_checkpoint",
        new_callable=AsyncMock,
    )
    scene_cap = patch(
        "backend.agent.tool_policy.scene_max_iterations",
        return_value=2,
    )
    registry_patcher.start()
    ck_m = ck.start()
    scene_cap.start()
    p_ac = patch.object(_st, "agent_auto_continue", True)
    p_seg = patch.object(_st, "agent_auto_continue_max_segments", 3)
    p_thr = patch.object(_st, "agent_thrash_force_final_interactive", False)
    p_nar = patch.object(_st, "agent_no_autoresume_on_thrash", False)
    p_ac.start()
    p_seg.start()
    p_thr.start()
    p_nar.start()
    try:
        out = asyncio.run(_run_loop(loop, uuid.uuid4(), text="读一下 a.py"))
    finally:
        p_nar.stop()
        p_thr.stop()
        p_seg.stop()
        p_ac.stop()
        scene_cap.stop()
        ck.stop()
        registry_patcher.stop()
        _stop_loop(loop, patcher)

    assert ck_m.called, "segment boundary must reach save_checkpoint (not TypeError)"
    assert len(llm.calls) >= 3
    joined = " ".join(
        str(m.get("content") or "")
        for call in llm.calls
        for m in call["messages"]
    )
    assert "System auto-resume" in joined
    assert "续跑后的答复" in (out or "")


def test_no_write_ignored_nudge_force_final_stops_tools():
    """no_write nudges must force_final; model cannot ignore them for many rounds."""
    from backend.core.config import settings as _st

    def _read(i):
        return [
            _chunk(
                tool_call=_tc(f"r{i}", "file_read", {"path": f"f{i}.py"}),
                finish="tool_calls",
            )
        ]

    llm = _ScriptedLLMRespectTools(
        [_read(i) for i in range(12)]
        + [[_chunk(delta="审查结论：到此收束。", finish="stop")]]
    )
    loop, patcher = _make_loop(llm)
    loop.max_iterations = 20
    loop._execute_registered_tool = AsyncMock(return_value="ok")
    registry_patcher = patch(
        "backend.tools.registry.ToolRegistry.get",
        return_value=SimpleNamespace(parameters={"type": "object", "properties": {}}),
    )
    registry_patcher.start()
    patches = [
        patch("backend.agent.tool_policy.scene_max_iterations", return_value=20),
        patch.object(_st, "agent_auto_continue_max_segments", 1),
        patch.object(_st, "agent_no_write_nudge_after", 4),
        patch.object(_st, "agent_no_write_force_after", 2),
        patch.object(_st, "agent_converge_nudge_after", 99),
        patch.object(_st, "agent_soft_open_mode", True),
        patch.object(_st, "agent_thrash_force_final_interactive", False),
    ]
    for p in patches:
        p.start()
    try:
        out = asyncio.run(
            _run_loop(
                loop,
                uuid.uuid4(),
                text="帮我审查 https://github.com/wu1w/tevarn 的逻辑 bug",
            )
        )
    finally:
        for p in reversed(patches):
            p.stop()
        registry_patcher.stop()
        _stop_loop(loop, patcher)

    # first_at=4, grace=2 → force after 6 read rounds; +1 final LLM
    assert len(llm.calls) <= 8
    assert "不应再来" not in (out or "")
    last_tools = llm.calls[-1].get("tools")
    assert last_tools in (None, [])
    assert getattr(loop, "last_exit_reason", "") == "no_write_ignored"
