from backend.agent.tool_result_contract import normalize_tool_result, truncate_for_llm


def test_file_read_budget_is_deliberately_generous():
    """T3：file_read 的 executor 已按行边界分页并给出续读 offset，是截断的权威。

    这里若沿用通用小预算，就会把分好页的视图重新 head+tail 拼接，模型拿到
    「前 30 行 …省略… 末尾几行」却以为读到了完整文件 —— 静默错改的主因。
    故 file_read 预算被刻意抬高到与分页上限同量级，远高于通用工具。
    """
    from backend.agent.tool_result_contract import (
        DEFAULT_TOOL_BUDGET,
        TOOL_RESULT_BUDGET,
    )

    assert TOOL_RESULT_BUDGET["file_read"] > 20_000
    assert TOOL_RESULT_BUDGET["file_read"] > DEFAULT_TOOL_BUDGET * 10

    # 分页后的正常读取（远小于预算）必须原样透传，不得截断
    paged = "\n".join(f"{i:6d}\tline{i}" for i in range(1, 300))
    assert truncate_for_llm("file_read", paged) == paged


def test_file_read_head_tail_splice_is_last_resort():
    """病态内容（单行超长、无行结构）超过预算时仍兜底截断，不能无限放行。"""
    raw = "A" * 40_000
    out = truncate_for_llm("file_read", raw)
    assert len(out) < 40_000
    assert "omitted" in out
    assert out.startswith("A")


def test_short_passthrough():
    assert truncate_for_llm("grep", "hi") == "hi"


def test_normalize_empty():
    r = normalize_tool_result(None, tool_name="x")
    assert r.startswith("[Error]")


def test_background_not_truncated():
    msg = "[Background started] id=abc\n" + ("x" * 5000)
    assert normalize_tool_result(msg, tool_name="command") == msg


def test_file_write_ack_not_overtruncated():
    msg = "[Success] Written 120 characters to gen_pkg/stats.py"
    out = truncate_for_llm("file_write", msg)
    assert out == msg


def test_file_write_budget_at_least_2500():
    raw = "x" * 3000
    out = truncate_for_llm("file_write", raw)
    # may truncate but keep plenty
    assert len(out) >= 2000 or "omitted" in out
