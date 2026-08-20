"""User-channel: thinking stripped, pre-tool essays hidden, wrap-up stop."""

from backend.agent.thinking_format import wrap_thinking
from backend.agent.user_channel import (
    content_for_chat_persist,
    looks_like_complete_final_answer,
    looks_like_in_progress_narration,
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


def test_user_visible_escapes_backticked_think_and_keeps_tail():
    """Old frontend ate everything after a documented `<think>` mention."""
    tail = "KEEP_TAIL_SECTION_AFTER_THINK_MENTION"
    prose = (
        "上次被截断了，这里补完整版。\n\n"
        "## 2. 模型层\n\n"
        "把更早的 `<think>` 从发给模型的历史里删掉\n"
        "闭合的 `</think>` 也不该吞掉后文\n\n"
        "## 3. 体验层：即使用户仍在看思考块\n\n"
        f"{tail}\n\n"
        "## 4. 下一步\n"
        "## 5. Hermes 配置\n"
        "请贴你的 Hermes 配置。"
    )
    out = user_visible_content(prose)
    assert tail in out
    assert "## 5. Hermes 配置" in out
    assert "请贴你的 Hermes 配置。" in out
    assert out.index(tail) > out.index("&lt;think")
    # no raw opener left for parseMessageContent /<think\\b/ to eat
    assert "<think" not in out
    assert "<thinking" not in out
    assert "</think" not in out
    assert "&lt;think" in out
    assert "&lt;/think" in out


def test_user_visible_strips_leaked_stop_tokens():
    raw = "官方仓库正文再核一下核心循环和许可，避免只凭摘要下结论。\n<|eos|>"
    out = user_visible_content(raw)
    assert "<|eos|>" not in out
    assert "<|endoftext|>" not in user_visible_content("ok <|endoftext|>")
    assert "<|im_end|>" not in user_visible_content("ok <|im_end|>")
    assert "<|eot_id|>" not in user_visible_content("ok <|eot_id|>")
    assert "核心循环" in out


def test_user_visible_strips_file_start_wrap_and_progress():
    raw = (
        "官网和仓库已定位。接着抽 Qwen Code 文档里本地模型 / 小模型相关章节，"
        "避免只凭营销文案下结论。\n"
        "<file_start>官网和仓库已定位。接着抽 Qwen Code 文档里本地模型 / 小模型相关章节，"
        "避免只凭营销文案下结论。\n<file_end><|eos|>"
    )
    out = user_visible_content(raw)
    assert "<file_start>" not in out
    assert "<file_end>" not in out
    assert "<|eos|>" not in out
    assert "官网和仓库已定位" in out
    assert out.count("官网和仓库已定位") == 1
    assert looks_like_progress_note(raw)


def test_user_visible_strips_unique_call_id_dump():
    raw = (
        "先读官方仓库和文档正文，再下结论。不重复搜。\n"
        "<|uniquecall_id|>5</uniquecall_id>result_load<|uniqueid|>id</uniqueid>"
        "74e40399be6848e3</uniquecall>"
    )
    out = user_visible_content(raw)
    assert "uniquecall" not in out.lower()
    assert "74e40399be6848e3" not in out
    assert "先读官方仓库" in out


def test_short_factual_answer_is_not_in_progress_narration():
    """Freeze path: '文件内容是 X' must still finalize (not a progress aside)."""
    assert looks_like_in_progress_narration("文件内容是 X") is False
    assert looks_like_in_progress_narration("在的，有什么事？") is False
    assert looks_like_in_progress_narration("正在读取仓库关键文件…") is True
    assert looks_like_in_progress_narration("接着抽官方仓库正文再核。") is True

