"""Thin-control invariants (no loop thickening).

Pins regressions from soft-open thrash research:
- llm_round must not wait_for(__anext__) (cancels SSE mid-reasoning)
- empty_content after force_final must break (not infinite continue)
- solo/plan/continue classification matrix
- thrash exit reasons include empty_content_thrash
- codex reasoning coerce strips summary_text dict garbage
"""

from __future__ import annotations

import ast
from pathlib import Path


def test_llm_round_no_wait_for_anext():
    """AST guard: wait_for(__anext__) cancels the stream generator."""
    root = Path(__file__).resolve().parents[1]
    src = (root / "agent" / "phases" / "llm_round.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    bad: list[str] = []

    class V(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
            # asyncio.wait_for(something.__anext__(), ...)
            fn = node.func
            is_wait_for = (
                isinstance(fn, ast.Attribute) and fn.attr == "wait_for"
            ) or (isinstance(fn, ast.Name) and fn.id == "wait_for")
            if is_wait_for and node.args:
                arg0 = node.args[0]
                if isinstance(arg0, ast.Call) and isinstance(arg0.func, ast.Attribute):
                    if arg0.func.attr == "__anext__":
                        bad.append(f"L{node.lineno}")
            self.generic_visit(node)

    V().visit(tree)
    assert not bad, f"llm_round must not wait_for(__anext__): {bad}"
    # Positive: cooperative poll pattern present
    assert "create_task" in src and "__anext__" in src


def test_empty_content_after_force_final_breaks():
    """force_final + still empty → break (empty_content_thrash), not continue."""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from backend.agent.phases.no_tool_round import run_no_tool_round
    from backend.agent.turn_retry import TurnRetryState

    loop = SimpleNamespace(
        _should_stop=False,
        last_exit_reason="",
        _push_status=AsyncMock(),
        model="test",
        _model_name="test",
    )
    tr = TurnRetryState()

    async def _run():
        return await run_no_tool_round(
            loop,
            session_id=__import__("uuid").uuid4(),
            iteration=0,
            seg_size=40,
            messages=[],
            accumulated_content="",
            accumulated_reasoning="",
            goal_mode=False,
            goal_nudge_count=0,
            turn_retry=tr,
            empty_reply_max=2,
            last_tool_round_count=0,
            force_final_no_tools=True,  # already force_final
            user_input="继续下一步",
            enriched_input="继续下一步",
            tools_used_run=[],
            completion_followups=0,
        )

    result = asyncio.get_event_loop().run_until_complete(_run())
    assert result.action == "break"
    assert getattr(loop, "last_exit_reason", "") == "empty_content_thrash"


def test_solo_matrix_continue_vs_plan_read():
    from backend.agent.simple_intent import (
        is_plan_or_read_intent,
        is_solo_session_intent,
    )
    from backend.agent.robust import is_continue_phrase
    from backend.agent.goal_facade import looks_like_goal_continue
    from backend.agent.vague_intent import is_vague_work_intent

    # Continue / execute work — full coding surface, not plan-solo
    assert not is_solo_session_intent("继续下一步")
    assert not is_plan_or_read_intent("继续下一步")
    assert not is_solo_session_intent("请继续")
    assert not is_solo_session_intent("规划下一步的任务并开始执行")
    assert not is_plan_or_read_intent("规划下一步的任务并开始执行")
    assert is_continue_phrase("那你接着下一项工作")
    assert looks_like_goal_continue("那你接着下一项工作")
    assert not is_solo_session_intent("那你接着下一项工作")
    assert not is_vague_work_intent("那你接着下一项工作")  # continue, not vague invent

    # Plan / read — solo
    assert is_plan_or_read_intent("读一下文档，总结现状")
    assert is_solo_session_intent("读一下文档，总结现状")
    assert is_solo_session_intent("帮我做个 M0 计划")

    # Casual simple
    assert is_solo_session_intent("今天天气怎么样")

    # Team wins
    assert not is_solo_session_intent("派给工程师改登录页")

    # Vague work — prefer clarify path (not continue)
    assert is_vague_work_intent("帮我弄好")
    assert is_vague_work_intent("你看着办")
    assert not is_vague_work_intent("按照工程师的建议修复问题")
    assert not is_vague_work_intent("直接执行，别问了")


def test_thrash_exit_reasons_include_empty_and_stream():
    from backend.agent.goal_facade import THRASH_EXIT_REASONS, is_thrash_exit_reason

    assert is_thrash_exit_reason("empty_content_thrash")
    assert is_thrash_exit_reason("llm_stream_error")
    assert is_thrash_exit_reason("doom_loop")
    assert is_thrash_exit_reason("THRASH")  # case
    assert not is_thrash_exit_reason("max_tool_rounds")
    assert "empty_content_thrash" in THRASH_EXIT_REASONS
    assert "llm_stream_error" in THRASH_EXIT_REASONS


def test_codex_coerce_reasoning_summary_text():
    from backend.api.routes.openai_codex_proxy import (
        _coerce_reasoning_text,
        _CodexStreamToChat,
    )

    assert (
        _coerce_reasoning_text([{"type": "summary_text", "text": "hello world"}])
        == "hello world"
    )
    dirty = str([{"type": "summary_text", "text": "clean"}])
    assert _coerce_reasoning_text(dirty) == "clean"
    assert "summary_text" not in _coerce_reasoning_text(dirty)

    c = _CodexStreamToChat("gpt-test")
    parts = c.feed(
        {
            "type": "response.output_item.done",
            "item": {
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": "plan A"}],
            },
        }
    )
    blob = "".join(parts)
    assert "plan A" in blob
    assert "summary_text" not in blob


def test_no_autoresume_on_thrash_default_true():
    from backend.core.config import settings

    assert bool(getattr(settings, "agent_no_autoresume_on_thrash", False)) is True
