"""正文伪 toolcall 回收。"""

from backend.agent.pseudo_tool_recover import (
    leak_stop_final_text,
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


def test_arg_key_leak_is_scrubbed_from_user_text():
    raw = (
        "工作区有未提交改动。对照上次红项：跑定向 pytest。"
        "command<arg_key>command<arg_value>cd /Users/william/tevarn && echo venv"
    )
    assert looks_like_pseudo_tool_content(raw)
    cleaned = scrub_leak_markers(raw)
    assert "arg_key" not in cleaned
    assert "cd /Users/william" not in cleaned
    assert "跑定向 pytest" in cleaned


def test_broken_name_json_and_brace_loop_scrubbed():
    raw = (
        "文档目录和许可已经齐了。再读 Loop Engineering 正文和本地模型部分，然后给你结论。\n"
        '{\n "name": "result_load",Loop Engineering 文档路径已确认，正在拉正文。\n'
        "} \n} \n} \n} \n} \n} \n} \n} \n} \n} \n} \n} \n}"
    )
    assert looks_like_pseudo_tool_content(raw)
    cleaned = scrub_leak_markers(raw)
    assert "result_load" not in cleaned
    assert cleaned.count("}") <= 1
    assert "文档目录和许可已经齐了" in cleaned
    from backend.agent.user_channel import user_visible_content

    visible = user_visible_content(raw)
    assert "result_load" not in visible
    assert visible.count("}") <= 1
    assert "文档目录" in visible


def test_looks_like_token_loop_on_brace_run():
    from backend.agent.pseudo_tool_recover import looks_like_token_loop

    soup = "结论。\n" + "} \n" * 20
    assert looks_like_token_loop(soup, "} \n") is True
    assert looks_like_token_loop("ok {a:1}", "}") is False


_SENTENCE_LOOP = (
    "设置页和 README 已经拉到。接着只读 `fastModel` / 本地小模型相关段落。\n"
    "设置页很长，先定位 `fastModel` / 本地模型相关原文。\n"
    "设置页很长，先定位 `fastModel` / 本地模型相关原文再下结论。\n"
    "设置页很长，先定位 `fastModel` / 本地模型相关原文。\n"
    "设置页很长，先定位 `fastModel` / 本地模型相关原文再下结论。\n"
    "设置页很长，先定位 `fastModel` / 本地模型相关原文。\n"
    "设置页很长，先定位 `fastModel` / 本地模型相关原文再下结论。\n"
    "设置页很长，先定位 `fastModel` / 本地模型相关原文。\n"
)


def test_sentence_loop_collapsed_and_detected():
    from backend.agent.pseudo_tool_recover import (
        collapse_repetition_tail,
        looks_like_token_loop,
    )
    from backend.agent.user_channel import user_visible_content

    assert looks_like_token_loop(_SENTENCE_LOOP) is True
    out = collapse_repetition_tail(_SENTENCE_LOOP)
    assert out.count("设置页很长") == 1
    assert "已经拉到" in out
    vis = user_visible_content(_SENTENCE_LOOP)
    assert vis.count("设置页很长") == 1
    # Distinct review bullets must not collapse.
    review = (
        "- `loop.py` 把 native reasoning 包进 thinking 标签\n"
        "- `loop.py` 在工具轮继续 glob/file_read\n"
        "- 首轮在调用 use_tool_pack 前编造项目简介\n"
    )
    assert collapse_repetition_tail(review) == review.rstrip()
    assert looks_like_token_loop(review) is False


_BLOCK_LOOP = (
    "HTTP 组包还没读到，只抽 `build_body` 和 kwargs 落点。组包函数名是 "
    "`build_chat_body`，不是 `build_body`。只抽这一段。\n"
    + (
        "The function is `build_chat_body`, not `build_body`. Extract that plus Sampling.\n"
        "I should also check README since they asked for project docs.\n"
        "Given the user asked to re-read project docs AND provided the test endpoint, "
        "I think they want:\n"
        "1. Re-read current q-harness (they've been changing it)\n"
        "2. Possibly test against the live endpoint\n"
        "I'll extract build_chat_body then write the analysis.\n"
        "Let me get build_chat_body.组包函数名是 `build_chat_body`。只抽这一段和 sampling 表。\n"
    )
    * 6
    + (
        "The function is `build_chat_body`. Extract that plus Sampling. "
        "Then I have enough to write the analysis.\n"
        "I should also check README since they asked for project docs. "
        "And whether they've updated design.md vs code.\n"
        "Given the user asked to re-read project docs AND provided the test endpoint, "
        "I think they want:\n"
        "1. Re-read current q-harness (they've been changing it)\n"
        "2. Possibly test against the live endpoint\n"
    )
    * 4
)


def test_drifting_planning_block_loop_collapsed_and_detected():
    """Live: English planning paragraphs cycled with drift, not the same last line."""
    from backend.agent.pseudo_tool_recover import (
        collapse_repetition_tail,
        looks_like_token_loop,
    )
    from backend.agent.user_channel import user_visible_content

    assert looks_like_token_loop(_BLOCK_LOOP) is True
    out = collapse_repetition_tail(_BLOCK_LOOP)
    assert out.count("The function is") == 1
    assert out.count("HTTP 组包还没读到") == 1
    assert "build_chat_body" in out
    vis = user_visible_content(_BLOCK_LOOP)
    assert vis.count("The function is") == 1
    assert vis.count("I should also check README") <= 1
    assert len(vis) < len(_BLOCK_LOOP) * 0.4


def test_trailing_lone_brace_stripped():
    from backend.agent.pseudo_tool_recover import collapse_repetition_tail

    raw = "文档目录和许可已经齐了。再读正文，然后给你结论。\n\n}"
    assert collapse_repetition_tail(raw).endswith("结论。")


def test_grok_xml_tool_calls_recovered_and_scrubbed():
    raw = (
        "提取结果里有两边仓库正文。先分页读源码结构与循环实现，再下结论。\n"
        "<|tool_calls_section_begin|>\n"
        "<|tool_call_begin|>\n"
        "<|tool_call_begin|>\n"
        "result_load\n"
        "id\n"
        "56a93fe9f7c249ca\n"
        "offset\n"
        "0\n"
        "max_chars\n"
        "22000\n"
        "<|tool_call_end|>\n"
        "<|tool_call_begin|>\n"
        "result_load\n"
        "id\n"
        "56a93fe9f7c249ca\n"
        "offset\n"
        "22000\n"
        "max_chars\n"
        "22000\n"
        "<|tool_call_end|>\n"
        "<|tool_calls_section_end|>\n"
    )
    assert looks_like_pseudo_tool_content(raw)
    schema = {"result_load", "mcp_tavily_search"}
    tcs, cleaned = recover_tool_calls_from_content(raw, schema_names=schema)
    assert len(tcs) == 2
    assert all(t.name == "result_load" for t in tcs)
    assert tcs[0].arguments.get("id") == "56a93fe9f7c249ca"
    assert tcs[0].arguments.get("offset") == 0
    assert tcs[1].arguments.get("offset") == 22000
    assert "tool_call_begin" not in cleaned
    assert "result_load" not in cleaned
    assert "先分页读" in cleaned
    from backend.agent.user_channel import user_visible_content

    visible = user_visible_content(raw)
    assert "tool_call" not in visible
    assert "先分页读" in visible


_UNIQUE_LEAK = (
    "先读官方仓库和文档正文，再下结论。不重复搜。"
    "设置页已出现 `fastModel` / `compactionModel`。继续读 README 和模型相关章节。\n"
    "<|uniquecall_id|>5</uniquecall_id>result_load<|uniqueid|>id</uniqueid>"
    "74e40399be6848e3<|uniqueoffset|>offset</uniqueoffset>22000"
    "<|uniquemax_chars|>max_chars</uniquemax_chars>22000</uniquecall> \n"
    "<|uniquecall_id|>6</uniquecall_id>result_load<|uniqueid|>id</uniqueid>"
    "98baa169cda04372<|uniqueoffset|>offset</uniqueoffset>0"
    "<|uniquemax_chars|>max_chars</uniquemax_chars>18000</uniquecall> \n"
    "<|uniquecall_id|>8</uniquecall_id>mcp_tavily_extract<|uniqueurls|>urls</uniqueurls>"
    '["https://raw.githubusercontent.com/QwenLM/qwen-code/main/README.md"]'
    "</uniquecall> \n"
    "<|uniquecall_id|>9</uniquecall_id>mcp_tavily_search<|uniquequery|>query</uniquequery>"
    "Qwen Code local 4B"
    "<|uniquemax_results|>max_results</uniquemax_results>6"
    "</uniquecall"
)


def test_unique_call_id_leak_is_detected_and_scrubbed():
    assert looks_like_pseudo_tool_content(_UNIQUE_LEAK)
    cleaned = scrub_leak_markers(_UNIQUE_LEAK)
    assert "uniquecall" not in cleaned.lower()
    assert "<|unique" not in cleaned
    assert "74e40399be6848e3" not in cleaned
    assert "先读官方仓库" in cleaned
    from backend.agent.user_channel import user_visible_content

    visible = user_visible_content(_UNIQUE_LEAK)
    assert "uniquecall" not in visible.lower()
    assert "result_load" not in visible
    assert "先读官方仓库" in visible


def test_unique_call_id_recovers_result_load_only():
    schema = {"result_load", "mcp_tavily_search", "mcp_tavily_extract"}
    tcs, cleaned = recover_tool_calls_from_content(
        _UNIQUE_LEAK, schema_names=schema
    )
    assert len(tcs) == 2
    assert all(t.name == "result_load" for t in tcs)
    assert tcs[0].arguments.get("id") == "74e40399be6848e3"
    assert tcs[0].arguments.get("offset") == 22000
    assert tcs[0].arguments.get("max_chars") == 22000
    assert tcs[1].arguments.get("id") == "98baa169cda04372"
    assert "uniquecall" not in cleaned.lower()
    assert "先读官方仓库" in cleaned


def test_use_tool_pack_not_recovered_from_body():
    content = (
        "<tool_call>\nuse_tool_pack\n"
        '{"packs": ["manage", "coding"]}\n</tool_call>'
    )
    tcs, _ = recover_tool_calls_from_content(content)
    assert tcs == []


def test_leak_stop_final_uses_scrubbed_or_short_stop():
    raw = (
        "Compact 是同一轮召回的核心。\n"
        '<tool_call>\ncommand\n{"command": "cat compact.rs"}\n</tool_call>\n'
    )
    out = leak_stop_final_text(raw)
    assert "tool_call" not in out
    assert "Compact" in out
    only_leak = '<tool_call>\ncommand\n{"command": "cat x"}\n</tool_call>'
    stopped = leak_stop_final_text(only_leak)
    assert "这一轮先停" in stopped
    assert "tool_call" not in stopped
