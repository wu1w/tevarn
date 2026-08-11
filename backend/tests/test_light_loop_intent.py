# -*- coding: utf-8 -*-
"""Grok-style harness intent + matching MCP (Python fallback path)."""
from __future__ import annotations

from backend.agent.simple_intent import (
    HARNESS_LIMITS,
    classify_loop_intent,
    is_light_loop_intent,
    is_mcp_configure_intent,
    is_mcp_use_intent,
    is_mid_ops_loop_intent,
    resolve_harness_bundle,
    resolve_harness_mode,
    select_live_mcp_tools,
)


def test_classify_l0_paths_and_oauth():
    assert classify_loop_intent("告诉我你在本机的所有运行路径") == "L0"
    assert classify_loop_intent("给我一个oauth的地址") == "L0"
    assert is_light_loop_intent("天气怎么样")
    b = resolve_harness_bundle("天气怎么样")
    assert b["harness_mode"] == "chat"
    assert 6 <= int(b["max_iters"]) <= 12  # Grok thin hard cap (not 5, not 40)


def test_l0_does_not_false_positive_engineering_path():
    assert classify_loop_intent("为什么 workspace 编译失败") == "L3"
    assert classify_loop_intent("帮我看看执行路径怎么优化") == "L3"
    assert classify_loop_intent("继续实现登录页") == "L3"


def test_github_review_is_coding_not_l0():
    """Regression: GitHub review must not collapse to L0 max_iters=5."""
    assert resolve_harness_mode("帮我 review 这个 GitHub PR") == "coding"
    assert classify_loop_intent(
        "帮我 review https://github.com/x/y/pull/12"
    ) == "L3"


def test_classify_l1_mcp_ops_only_configure():
    assert classify_loop_intent("我给你加了豆包搜索MCP，你配下api key") == "L1"
    assert is_mcp_configure_intent("配下 tavily 的 api key")
    # 用 MCP 搜索 ≠ L1 配置
    assert classify_loop_intent("用 tavily 搜 AI 新闻") == "L0"
    assert is_mcp_use_intent("用 tavily 搜 AI 新闻")
    assert not is_mcp_configure_intent("用 tavily 搜 AI 新闻")


def test_mcp_matching_only_no_full_dump():
    live = [
        "mcp_tavily_search",
        "mcp_firecrawl_scrape",
        "mcp_github_list_issues",
        "mcp_other_foo",
    ]
    sel = select_live_mcp_tools(
        "用 tavily 搜 AI 新闻", live, matching_only=True, server_map={}
    )
    assert sel == ["mcp_tavily_search"]
    # configure must not dump live schemas
    assert select_live_mcp_tools("配下 tavily 的 api key", live, server_map={}) == []
    assert select_live_mcp_tools("随便聊聊", live, server_map={}) == []


def test_mcp_matching_custom_server_by_name():
    """自定义 MCP：用户点名 server，不靠预制白名单。"""
    from backend.agent.simple_intent import match_mcp_tools_flexible

    live = [
        "mcp_search",
        "mcp_create_page",
        "mcp_tavily_search",
    ]
    smap = {
        "mcp_search": "notion",
        "mcp_create_page": "notion",
        "mcp_tavily_search": "tavily",
    }
    sel = match_mcp_tools_flexible("用 notion 查一下页面", live, smap)
    assert "mcp_search" in sel
    assert "mcp_create_page" in sel
    assert "mcp_tavily_search" not in sel
    # 工具名自带 slug 也可
    live2 = ["mcp_my_crm_query", "mcp_tavily_search"]
    sel2 = select_live_mcp_tools(
        "用 my-crm 查客户", live2, matching_only=True, server_map={}
    )
    assert sel2 == ["mcp_my_crm_query"]


def test_custom_skill_tool_matching_only():
    from backend.agent.tool_policy import select_live_custom_tools

    live = ["my_http_lookup", "other_tool", "invoice_export"]
    assert select_live_custom_tools("用 my_http_lookup 查一下", live) == [
        "my_http_lookup"
    ]
    assert select_live_custom_tools("导出 invoice", live) == ["invoice_export"]
    assert select_live_custom_tools("随便聊聊", live) == []


def test_classify_l2_ops():
    assert classify_loop_intent("帮我用代理 tunnel 启动 codex") == "L2"
    assert resolve_harness_mode("帮我用代理 tunnel 启动 codex") == "ops"


def test_classify_l3_engineering():
    assert classify_loop_intent("重构 backend/agent/loop.py 并补 pytest") == "L3"
    assert classify_loop_intent("派给工程师做", workforce=False) == "L3"


def test_workforce_always_l3():
    assert classify_loop_intent("看下天气", workforce=True) == "L3"
    assert classify_loop_intent("hi", mode="cluster") == "L3"


def test_harness_limits_table():
    assert HARNESS_LIMITS["chat"]["max_iters"] == 8
    assert HARNESS_LIMITS["ops"]["max_iters"] == 12
    assert HARNESS_LIMITS["coding"]["max_iters"] == 20


def test_light_helpers_match_bundle_flags():
    # L1 configure is light_loop, not mid-ops
    b = resolve_harness_bundle("配下 tavily 的 api key")
    assert b["light_loop"] is True
    assert b["ops_loop"] is False
    assert is_light_loop_intent("配下 tavily 的 api key")
    assert not is_mid_ops_loop_intent("配下 tavily 的 api key")
    # real ops
    assert is_mid_ops_loop_intent("帮我用代理 tunnel 启动 codex")
    # auto_continue off for thin
    assert b.get("auto_continue") is False
    assert int(b.get("max_segments") or 0) == 1


def test_no_write_a_false_positive_py():
    assert resolve_harness_mode("write a short summary of the meeting") == "chat"
