"""正文伪 toolcall 回收。"""

from backend.agent.pseudo_tool_recover import (
    looks_like_pseudo_tool_content,
    recover_tool_calls_from_content,
    scrub_leak_markers,
)
from backend.agent.decisive import thrash_fingerprint, family_bucket


def test_recover_xml_tool_call():
    content = (
        "我来配置：\n"
        "<tool_call>\n"
        "manage_mcp\n"
        '{"action": "list"}\n'
        "</tool_call>\n"
        "稍等"
    )
    tcs, cleaned = recover_tool_calls_from_content(content)
    assert len(tcs) == 1
    assert tcs[0].name == "manage_mcp"
    assert tcs[0].arguments.get("action") == "list"


def test_recover_nested_env_json():
    content = (
        "```json\n"
        '{"name": "manage_mcp", "arguments": {"action": "update", "name": "tavily",'
        ' "env": {"TAVILY_API_KEY": "sk-nested-key-abcdefgh"}}}\n'
        "```"
    )
    tcs, cleaned = recover_tool_calls_from_content(content)
    assert len(tcs) == 1
    env = tcs[0].arguments.get("env") or {}
    assert env.get("TAVILY_API_KEY", "").startswith("sk-nested")


def test_broken_json_not_recovered_as_raw():
    content = '<tool_call>\nmanage_mcp\n{action: list}\n</tool_call>'
    tcs, cleaned = recover_tool_calls_from_content(content)
    assert tcs == []
    assert looks_like_pseudo_tool_content(content)


def test_no_recover_non_whitelist():
    content = '<tool_call>command\n{"command": "rm -rf /"}\n</tool_call>'
    tcs, cleaned = recover_tool_calls_from_content(content)
    assert tcs == []


def test_dsml_invoke_recover_with_schema():
    """DeepSeek DSML 正文泄漏 → 回收为 native tool_calls（须在 schema 内）。"""
    content = (
        "信息已经比较全了，我再定向验证。\n\n"
        "<|DSML|tool_calls>\n"
        '<|DSML|invoke name="mcp_tavily_search">\n'
        '<|DSML|parameter name="max_results" string="false">6</|DSML|parameter>\n'
        '<|DSML|parameter name="query" string="true">'
        "DeepWiki MCP remote endpoint</|DSML|parameter>\n"
        "</|DSML|invoke>\n"
        '<|DSML|invoke name="mcp_tavily_search">\n'
        '<|DSML|parameter name="query" string="true">'
        "Context7 MCP no API key</|DSML|parameter>\n"
        "</|DSML|invoke>\n"
        "</|DSML|tool_calls>\n"
    )
    assert looks_like_pseudo_tool_content(content)
    # 无 schema：默认白名单不含 mcp_tavily_search → 不回收
    tcs0, _ = recover_tool_calls_from_content(content)
    assert tcs0 == []
    # 有 schema：应回收
    schema = {"mcp_tavily_search", "mcp_web_search", "result_load", "clarify"}
    tcs, cleaned = recover_tool_calls_from_content(content, schema_names=schema)
    assert len(tcs) == 2
    assert all(t.name == "mcp_tavily_search" for t in tcs)
    assert "DeepWiki" in str(tcs[0].arguments.get("query") or "")
    assert tcs[0].arguments.get("max_results") == 6
    assert "DSML" not in cleaned
    assert "invoke" not in cleaned.lower()
    scrubbed = scrub_leak_markers(content)
    assert "DSML" not in scrubbed or "invoke name" not in scrubbed.lower()


def test_dsml_command_not_recovered_even_in_schema():
    content = (
        '<|DSML|invoke name="command">\n'
        '<|DSML|parameter name="command" string="true">rm -rf /</|DSML|parameter>\n'
        "</|DSML|invoke>\n"
    )
    tcs, _ = recover_tool_calls_from_content(
        content, schema_names={"command", "mcp_tavily_search"}
    )
    assert tcs == []


def test_mcp_ops_fingerprint_differs_by_action():
    class TC:
        def __init__(self, name, arguments=None):
            self.name = name
            self.arguments = arguments or {}

    fp1 = thrash_fingerprint([TC("manage_mcp", {"action": "list"})])
    fp2 = thrash_fingerprint([TC("manage_mcp", {"action": "update"})])
    assert family_bucket([TC("manage_mcp")]) == "mcp_ops"
    assert fp1 != fp2
