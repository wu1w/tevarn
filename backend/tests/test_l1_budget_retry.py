"""L1 unit tests: budget + turn retry + tool result contract."""
from __future__ import annotations

from backend.agent.iteration_budget import IterationBudget
from backend.agent.tool_result_contract import is_tool_error, normalize_tool_result
from backend.agent.turn_retry import RetryKind, TurnRetryState, classify_llm_error


def test_budget_consume_and_exhaust():
    b = IterationBudget(3)
    assert b.consume() and b.consume() and b.consume()
    assert not b.consume()
    assert b.remaining == 0
    assert b.used == 3
    b.refund()
    assert b.remaining == 1
    assert b.consume()


def test_turn_retry_empty_then_force_final():
    st = TurnRetryState()
    # caps empty_content default 2 → 1st/2nd retry, 3rd force_final
    assert st.note_and_decide(RetryKind.EMPTY_CONTENT) == "retry"
    assert st.note_and_decide(RetryKind.EMPTY_CONTENT) == "retry"
    assert st.note_and_decide(RetryKind.EMPTY_CONTENT) == "force_final"


def test_turn_retry_content_filter_stops():
    st = TurnRetryState()
    assert st.note_and_decide(RetryKind.CONTENT_FILTER) == "stop"


def test_turn_retry_thrash_force_final():
    st = TurnRetryState()
    assert st.note_and_decide(RetryKind.THRASH) == "force_final"


def test_classify_rate_limit():
    assert classify_llm_error("Error 429 rate limit") == RetryKind.RATE_LIMIT


def test_normalize_tool_result():
    assert normalize_tool_result(None, tool_name="x").startswith("[Error]")
    long = "a" * 50_000
    out = normalize_tool_result(long, max_chars=1000, tool_name="t")
    assert len(out) < 1200
    assert "截断" in out
    assert is_tool_error("[Error] boom")
    assert not is_tool_error("ok")
