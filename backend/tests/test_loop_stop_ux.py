"""Loop start/stop + end-reply UX: chat copy must match mainstream agents."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from backend.agent.exit_reasons import format_exit_user_message
from backend.agent.thinking_format import ensure_user_facing_final

ROOT = Path(__file__).resolve().parents[2]


def test_chat_exit_copy_has_no_operator_leak():
    for code in (
        "budget_exhausted",
        "kernel_iteration_exhausted",
        "doom_loop",
        "stopped_by_user",
        "kernel_budget_precheck",
        "empty_content_thrash",
        "host_down",
    ):
        msg = format_exit_user_message(code, process_id="proc-secret")
        assert "proc-secret" not in msg
        assert "process_id=" not in msg
        assert "/api/" not in msg
        assert "/kernel/" not in msg
        assert "top_up" not in msg
        assert "Rust" not in msg
        assert "decision_trail" not in msg
    assert "请继续" in format_exit_user_message("budget_exhausted")


def test_operator_copy_still_has_process_id():
    op = format_exit_user_message(
        "kernel_iteration_exhausted", process_id="abc123", for_operator=True
    )
    assert "abc123" in op


def test_user_stop_empty_final_is_blank():
    assert (
        ensure_user_facing_final(
            "",
            user_input="hello",
            exit_reason="stopped_by_user",
        )
        == ""
    )
    kept = ensure_user_facing_final(
        "这里是已经流出的半句",
        exit_reason="stopped_by_user",
    )
    assert "这里是已经流出的半句" in kept


def test_persist_skips_empty_user_stop():
    src = (ROOT / "backend" / "agent" / "loop_io.py").read_text(encoding="utf-8")
    assert "stopped_by_user" in src
    assert "skip_if_empty" in src
    assert "本轮未生成可见正文" not in src


def test_loop_stop_no_english_placeholder():
    loop_src = (ROOT / "backend" / "agent" / "loop.py").read_text(encoding="utf-8")
    llm_src = (ROOT / "backend" / "agent" / "phases" / "llm_round.py").read_text(
        encoding="utf-8"
    )
    assert "[Stopped] Generation was cancelled" not in loop_src
    assert "[Stopped] Generation was cancelled" not in llm_src
    assert "POST /api/sessions" not in loop_src
    assert "/kernel/policy" not in loop_src
    assert "format_exit_user_message" not in loop_src.split("return final_content")[-1]


def test_ws_stop_status_is_user_facing():
    src = (ROOT / "backend" / "api" / "websocket.py").read_text(encoding="utf-8")
    assert "Generation stopped by user" not in src
    assert '"detail": "已停止"' in src
    assert "Agent error:" not in src
    assert "已排队" in src


def test_chat_page_keeps_stop_partial():
    src = (ROOT / "frontend" / "app" / "chat" / "page.tsx").read_text(encoding="utf-8")
    assert "keepPartialAssistantOnIdle" in src
    assert "_${streamStatusDetail}_" not in src
    assert "mapStreamStatusDetail" in src
    assert "if (leftover && !wasStopping)" not in src
    assert src.count("keepPartialAssistantOnIdle") >= 3


def test_no_tool_round_no_english_stopped():
    src = (ROOT / "backend" / "agent" / "phases" / "no_tool_round.py").read_text(
        encoding="utf-8"
    )
    assert "[Stopped]" not in src
    assert "Model returned empty replies" not in src


def test_llm_round_chat_copy_no_operator_dump():
    src = (ROOT / "backend" / "agent" / "phases" / "llm_round.py").read_text(
        encoding="utf-8"
    )
    assert "禁止用报告框架" not in src
    assert "LLM 调用失败:" not in src
    assert "请在 PC 工作台" not in src
    assert "top_up 或提高" not in src
    assert "[Scheduler]" not in src
    assert "内核「调度」" not in src
    assert "_err_chunk" in src
    assert "出错了，请再试一次。" in src


def test_empty_thrash_final_is_readable():
    msg = format_exit_user_message("empty_content_thrash")
    assert "没有生成可见回复" in msg
    assert "工具轮次" not in msg
    empty = ensure_user_facing_final("", exit_reason="empty_content_thrash")
    assert "没有生成可见回复" in empty


def test_llm_round_sets_stopped_reason():
    src = (ROOT / "backend" / "agent" / "phases" / "llm_round.py").read_text(
        encoding="utf-8"
    )
    assert 'last_exit_reason = "stopped_by_user"' in src


def test_cancel_cleanup_persists_draft():
    src = (ROOT / "backend" / "agent" / "loop.py").read_text(encoding="utf-8")
    assert "_streamed_visible" in src
    assert "skip_if_empty=True" in src
    assert "_persist_final_response(" in src


def test_gate_stop_keeps_existing_draft():
    src = (ROOT / "backend" / "agent" / "loop.py").read_text(encoding="utf-8")
    gate = src.split('if _gate == "stop":', 1)[1].split("break", 1)[0]
    assert "accumulated_content" in gate
    assert "format_exit_user_message" in gate


def test_pretool_retract_clears_streamed_visible():
    src = (ROOT / "backend" / "agent" / "phases" / "llm_round.py").read_text(
        encoding="utf-8"
    )
    fn = src.split("async def _retract_pretool_user_stream", 1)[1].split(
        "async def run_llm_round", 1
    )[0]
    assert "_streamed_visible" in fn


def test_fail_cleanup_persists_draft_like_cancel():
    src = (ROOT / "backend" / "agent" / "loop.py").read_text(encoding="utf-8")
    window = src.split("async def _cleanup_fail()", 1)[1][:1200]
    assert "fail persist draft skip" in window
    assert "skip_if_empty=True" in window


def test_iteration_consume_requires_allow_or_exhausted():
    src = (ROOT / "backend" / "agent" / "loop.py").read_text(encoding="utf-8")
    assert '"allow",' in src
    assert "_ic.get(\"status\") in (" in src or "_ic.get('status') in (" in src


def test_ws_agent_error_also_idles():
    src = (ROOT / "backend" / "api" / "websocket.py").read_text(encoding="utf-8")
    err = src.split('detail": "出错了，请再试一次。"', 1)[1][:800]
    assert '"state": "idle"' in err
