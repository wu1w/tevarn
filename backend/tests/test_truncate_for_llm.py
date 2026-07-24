from backend.agent.tool_result_contract import normalize_tool_result, truncate_for_llm


def test_truncate_file_read_budget():
    raw = "A" * 5000
    out = truncate_for_llm("file_read", raw)
    assert len(out) < 2500
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
