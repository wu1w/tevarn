"""Unit tests for thinking / reasoning presentation helpers."""

from backend.agent.thinking_format import (
    canonicalize_thinking,
    is_visible_empty,
    looks_like_force_final_report,
    sanitize_force_final_body,
    short_segment_handoff_message,
    strip_force_final_scare_for_context,
    strip_thinking,
    wrap_thinking,
)
from backend.agent.robust import is_empty_assistant_content


def test_wrap_thinking_with_body():
    out = wrap_thinking("step 1", "answer")
    assert out.startswith("<thinking>")
    assert "step 1" in out
    assert out.rstrip().endswith("answer")
    assert "</thinking>" in out


def test_wrap_thinking_empty_body():
    out = wrap_thinking("only reason", "")
    assert "<thinking>" in out
    assert "only reason" in out
    assert strip_thinking(out) == ""


def test_wrap_skips_double():
    already = "<thinking>\nx\n</thinking>\n\nbody"
    assert wrap_thinking("y", already) == already


def test_strip_and_empty():
    raw = wrap_thinking("hidden", "  visible  ")
    assert strip_thinking(raw) == "visible"
    assert not is_visible_empty(raw)
    assert is_visible_empty(wrap_thinking("only", ""))
    assert is_empty_assistant_content(wrap_thinking("only", ""))
    assert not is_empty_assistant_content(wrap_thinking("t", "hi"))


def test_canonicalize_dedupes_native_and_model_tags():
    content = "<thinking>\nmodel wrote this\n</thinking>\n\nvisible ok"
    out = canonicalize_thinking("native reasoning", content)
    assert out.count("<thinking>") == 1
    assert "native reasoning" in out
    assert "model wrote this" not in out
    assert strip_thinking(out) == "visible ok"


def test_canonicalize_no_native_keeps_model_tags():
    content = "<thinking>\nonly model\n</thinking>\n\nbody"
    assert canonicalize_thinking("", content) == content
    assert canonicalize_thinking(None, content) == content


def test_force_final_report_detection_and_sanitize():
    avalanche = "\n".join(
        [
            "改用 command 分批导出关键文件内容。",
            "改用 command 分批导出。",
            "改用 cmd 导出。",
            "列 crates 目录。",
            "继续 M0 对齐编译。",
            "继续推进 M0 对齐。",
            "继续修复编译对齐。",
            "验证 cargo check。",
            "运行 cargo check。",
            "检查编译。",
            "汇总并验证。",
            "收束并验证编译。",
            "按强制收束要求汇报。",
            "根据系统强制收束指令：**本轮禁止再调工具**，直接用中文汇报现状。",
            "## 强制收束报告（工具轮次已达硬顶）",
            "本轮被系统拦截，无法再调用任何工具。",
        ]
    )
    assert looks_like_force_final_report(avalanche)
    short = sanitize_force_final_body(
        avalanche, goal_mode=True, exit_code="max_tool_rounds", prefer_short=True
    )
    assert "工具轮" in short or "下一段" in short
    assert "强制收束报告" not in short
    assert len(short) < 200


def test_strip_force_final_for_context():
    body = (
        "<thinking>\nhidden\n</thinking>\n\n"
        "## 强制收束报告（工具轮次已达硬顶）\n"
        + "\n".join(f"继续状态句{i}。" for i in range(20))
    )
    cleaned = strip_force_final_scare_for_context(body)
    assert "强制收束报告" not in cleaned
    assert "thinking" not in cleaned.lower() or "hidden" not in cleaned


def test_token_budget_not_collapsed_to_segment_msg():
    msg = "【Token 额度将尽】进程 token 使用已接近上限。卡在鉴权。"
    out = sanitize_force_final_body(msg, goal_mode=False, exit_code="budget_ratio")
    assert "token" in out.lower() or "额度" in out
    assert short_segment_handoff_message(goal_mode=True)


def test_force_final_message_wording():
    from backend.agent.loop_guard_bridge import force_final_message

    m = force_final_message("max_tool_rounds")
    assert "工具轮" in m
    assert "硬顶" not in m
    assert "用中文列出已完成" not in m  # no inventory demand
    assert "Token 预算将尽" not in m
    b = force_final_message("budget_ratio")
    assert "Token" in b or "token" in b.lower() or "额度" in b
    assert "85%" not in b  # less scare; still distinct from tool rounds
    assert "工具轮" not in b


def test_ensure_keeps_streamed_body_no_excerpt_rewrite():
    """Good model/stream text must not be rewritten into 本段工作小结摘录."""
    from backend.agent.thinking_format import ensure_user_facing_final

    long_plan = (
        "## M0 计划\n\n"
        "1. 对齐编译与工具链\n"
        "2. 拆交付边界\n"
        "3. 验证路径\n\n"
        "细节说明……" * 5
    )
    final = ensure_user_facing_final(
        long_plan,
        user_input="帮我做个 M0 计划",
        messages=[{"role": "assistant", "content": "无关旧进度"}],
        goal_summary="# Goal: quality-review\nStatus: completed\n",
        tool_rounds=2,
        goal_mode=False,
    )
    assert final == long_plan
    assert "本段工作小结" not in final
    assert "从对话摘录" not in final

    # Stock handoff also kept as-is (no harvest rewrite)
    handoff = "本段工具轮次已用尽。可发送「请继续」或等待自动续跑开启下一段。"
    kept = ensure_user_facing_final(
        handoff,
        user_input="帮我做个 M0 计划",
        messages=[{"role": "assistant", "content": "M0 建议：先对齐编译。"}],
        tool_rounds=2,
        goal_mode=False,
    )
    assert kept == handoff
    assert "本段工作小结" not in kept

    # Only completely empty → minimal one-liner handoff
    empty = ensure_user_facing_final("", user_input="x", goal_mode=False)
    assert "本段工作小结" not in empty
    assert "摘录" not in empty
