"""User-channel: thinking stripped, pre-tool essays hidden, wrap-up stop."""

from backend.agent.thinking_format import wrap_thinking
from backend.agent.user_channel import (
    content_for_chat_persist,
    looks_like_complete_final_answer,
    looks_like_progress_note,
    should_stop_wrapup_redraft,
    user_visible_content,
)

_REVIEW = (
    "## 逻辑问题\n\n"
    "- `loop.py` 把 native reasoning 包进 thinking 标签再写入 messages.content，"
    "用户气泡能看到思考过程，持久化回放也会把 CoT 当正文。\n"
    "- 工具轮在已有完整审查稿后仍继续 glob/file_read，造成反复重写同一份结论。\n"
    "- 首轮在调用 use_tool_pack 前就编造了项目简介（Rust SOCKS5 HTTP 代理）。\n"
    "- CEO 身份默认「简单问答直接答」与仓库审查抢工具面，薄档会诱导先开口再扩包。\n\n"
    "## 结论\n\n"
    "这是一份完整审查：先读源码再下结论，不要编造 Tevarn 是 SOCKS5 代理。"
    "建议修复 persist 路径（strip thinking、tool 轮不落长文）、stream retract，"
    "以及完整稿去重停轮，避免 20+ 轮工具后还在重写同一份 review。\n"
    "证据来自 loop persist 与 llm_round 的流式组装，而不是猜测仓库用途。\n"
    "下一步只应在用户追问时再开工具，而不是自动再 glob 一遍。\n"
    "以上结论已足够作为本轮对用户的完整答复。\n"
)


def test_user_visible_strips_thinking_tags():
    raw = wrap_thinking("secret CoT about SOCKS5", "在的，有什么事？")
    assert "<thinking>" in raw
    out = user_visible_content(raw)
    assert "<thinking>" not in out
    assert "secret CoT" not in out
    assert "在的" in out


def test_chat_persist_hides_pretool_essay_keeps_progress():
    essay = (
        "Tevarn 是一个用 Rust 写的 SOCKS5 / HTTP 代理，主要提供流量转发。"
        "下面我来分析它的逻辑 bug。"
    )
    assert content_for_chat_persist(essay, has_tool_calls=True) == ""
    assert "SOCKS5" in content_for_chat_persist(essay, has_tool_calls=False)

    note = "正在读取仓库关键文件…"
    assert content_for_chat_persist(note, has_tool_calls=True) == note
    assert looks_like_progress_note(note)


def test_thinking_only_tool_turn_persists_empty():
    """Live: assistant row was only thinking tags + tool_calls, 0 visible text."""
    raw = wrap_thinking("secret CoT", "")
    assert "<thinking>" in raw
    persisted = content_for_chat_persist(raw, has_tool_calls=True)
    assert persisted == ""
    assert "<thinking>" not in persisted
    assert "secret CoT" not in persisted


def test_thinking_not_in_persisted_user_content_with_tools():
    raw = wrap_thinking("plan glob then read", "正在列出源码。")
    persisted = content_for_chat_persist(raw, has_tool_calls=True)
    assert "<thinking>" not in persisted
    assert "plan glob" not in persisted
    assert "列出源码" in persisted


def test_complete_answer_and_wrapup_requires_prior_tools():
    assert len(_REVIEW) >= 400
    assert looks_like_complete_final_answer(_REVIEW)
    # First-turn hallucination + tools must NOT stop (no evidence yet)
    assert should_stop_wrapup_redraft(
        current=_REVIEW, previous="", tool_rounds=0
    ) is False
    assert should_stop_wrapup_redraft(
        current=_REVIEW, previous=_REVIEW, tool_rounds=1
    ) is False
    # After evidence tools, a re-draft of a complete review stops
    assert should_stop_wrapup_redraft(
        current=_REVIEW, previous=_REVIEW, tool_rounds=2
    ) is True
    assert should_stop_wrapup_redraft(
        current="在的", previous=_REVIEW, tool_rounds=5
    ) is False


def test_ensure_user_facing_final_strips_thinking():
    from backend.agent.thinking_format import ensure_user_facing_final

    raw = wrap_thinking("hidden", "可见答复")
    out = ensure_user_facing_final(raw, user_input="在吗")
    assert out == "可见答复"
    assert "<thinking>" not in out
