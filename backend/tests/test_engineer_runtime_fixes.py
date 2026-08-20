"""Regressions from 工程师 / CEO 回调空转（2026-08-19）。"""

from __future__ import annotations

from types import SimpleNamespace

from backend.agent.grant_store import expand_implied_tool_caps, tool_matches_crew_caps
from backend.agent.write_intent import is_write_intent
from backend.kernel.process_access import process_public_dict
from backend.services.llm.http_session import first_event_timeout_seconds


def test_rollup_prompt_is_not_write_intent():
    prompt = (
        "【系统·编制自动回调】你派发的「Qwen3.8 裸测思维链」相关工单已全部结束。\n"
        "请立即用简短中文汇报。\n"
        "交卷写 qwen38-bare-probe-report.md。生成一份文档。\n"
    )
    assert is_write_intent(prompt) is False
    assert is_write_intent("请写一份 spec 文档") is True


def test_expand_implied_result_load():
    assert expand_implied_tool_caps(None) is None
    assert "result_load" in (expand_implied_tool_caps(["file_read"]) or [])
    assert "result_load" in (expand_implied_tool_caps(["file_rw"]) or [])
    assert "result_load" not in (expand_implied_tool_caps(["command"]) or [])
    already = expand_implied_tool_caps(["file_read", "result_load"]) or []
    assert already.count("result_load") == 1


def test_result_load_matches_file_rw_transitively():
    assert tool_matches_crew_caps("result_load", ["file_read"]) is True
    assert tool_matches_crew_caps("result_load", ["file_rw"]) is True
    assert tool_matches_crew_caps("result_load", ["command"]) is False


def test_process_public_dict_accepts_dict_token():
    proc = SimpleNamespace(
        to_dict=lambda: {
            "id": "p1",
            "state": "running",
            "token": {"id": "t1", "signature": "secret", "capabilities": ["file_read"]},
        },
        token={"id": "t1", "signature": "secret", "capabilities": ["file_read"]},
    )
    out = process_public_dict(proc)
    assert out["id"] == "p1"
    assert out["token"]["id"] == "t1"
    assert "signature" not in out["token"]


def test_first_event_timeout_reads_settings():
    from backend.core.config import settings

    old = settings.llm_stream_first_event_timeout_seconds
    try:
        settings.llm_stream_first_event_timeout_seconds = 180.0
        assert first_event_timeout_seconds() == 180.0
        settings.llm_stream_first_event_timeout_seconds = 0
        assert first_event_timeout_seconds() is None
    finally:
        settings.llm_stream_first_event_timeout_seconds = old
