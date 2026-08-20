"""result_load stays on web/thin surfaces and recovers without schema."""

from backend.agent.pseudo_tool_recover import extract_pseudo_tool_calls
from backend.agent.tool_policy import THIN_CHAT_TOOLS, TOOL_PACKS

_RESULT_LOAD_BODY = """仓库正文已经拉到了，先分页读核心 README，再给你能不能照搬的结论。
{
 "name": "result_load",
 "arguments": {
 "id": "04be9012cc464e4c",
 "offset": 8000,
 "max_chars": 16000
 }
}
"""


def test_result_load_in_web_pack_and_thin_chat():
    assert "result_load" in TOOL_PACKS["web"]
    assert "result_load" in THIN_CHAT_TOOLS
    if "mcp" in TOOL_PACKS:
        assert "result_load" in TOOL_PACKS["mcp"]


def test_extract_result_load_even_when_missing_from_schema():
    hits = extract_pseudo_tool_calls(
        _RESULT_LOAD_BODY,
        schema_names={"web_search", "mcp_tavily_search"},
    )
    assert hits
    name, args, _span = hits[0]
    assert name == "result_load"
    assert args.get("id") == "04be9012cc464e4c"
    assert args.get("offset") == 8000
    assert args.get("max_chars") == 16000
