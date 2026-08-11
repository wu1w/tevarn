"""正文伪 toolcall 回收。"""

from backend.agent.pseudo_tool_recover import (
    looks_like_pseudo_tool_content,
    recover_tool_calls_from_content,
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


def test_mcp_ops_fingerprint_differs_by_action():
    class TC:
        def __init__(self, name, arguments=None):
            self.name = name
            self.arguments = arguments or {}

    fp1 = thrash_fingerprint([TC("manage_mcp", {"action": "list"})])
    fp2 = thrash_fingerprint([TC("manage_mcp", {"action": "update"})])
    assert family_bucket([TC("manage_mcp")]) == "mcp_ops"
    assert fp1 != fp2
