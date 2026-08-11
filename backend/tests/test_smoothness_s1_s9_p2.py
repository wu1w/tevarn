"""S1–S9 + P2 smoke tests."""

def test_thin_chat():
    from backend.agent.tool_policy import is_thin_chat_intent, infer_scene, resolve_enabled_tool_names
    assert is_thin_chat_intent("你好")
    plan = infer_scene("你好", profile="dynamic")
    assert "auto_thin_chat" in (plan.reasons or []) or plan.injection_tier == "minimal"
    names, _ = resolve_enabled_tool_names(profile="dynamic", user_input="你好", mode="default")
    assert names is not None and "command" not in names

def test_mcp_add_custom():
    from backend.services.config_intent import detect_config_intent
    m = detect_config_intent("添加 mcp github 用 npx -y @modelcontextprotocol/server-github")
    assert m and m.kind == "mcp_add_custom"

def test_plan_intent():
    from backend.agent.plan_intent import is_plan_request, is_plan_approve
    assert is_plan_request("先做计划")
    assert is_plan_approve("批准计划")

def test_dynamic_default():
    from backend.core.config import settings
    assert getattr(settings, "agent_tool_profile", "") == "dynamic"
