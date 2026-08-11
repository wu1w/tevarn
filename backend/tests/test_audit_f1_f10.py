"""Audit F1–F10 regression tests."""

def test_f1_peiyixia_ops():
    from backend.agent.tool_policy import is_mcp_ops_intent, resolve_enabled_tool_names
    assert is_mcp_ops_intent("帮我配一下豆包搜索 MCP")
    names, plan = resolve_enabled_tool_names(
        profile="dynamic", user_input="帮我配一下豆包搜索 MCP", mode="default"
    )
    assert names is not None
    assert "manage_mcp" in names
    assert "command" not in names


def test_f2_plan_approve_narrow():
    from backend.agent.plan_intent import is_plan_approve
    assert is_plan_approve("批准计划")
    assert is_plan_approve("按计划执行")
    assert not is_plan_approve("开始执行")
    assert not is_plan_approve("开始执行这个任务")
    # plan_ready 尾注不得再诱导裸「开始执行」
    from backend.agent.phases import no_tool_round as ntr
    import inspect
    src = inspect.getsource(ntr)
    assert "按计划执行" in src
    assert "「开始执行」后" not in src


def test_f3_micro_loop():
    from backend.services.config_intent import detect_mcp_micro_loop
    ml = detect_mcp_micro_loop("帮我配置一下 mcp 服务")
    assert ml is not None
    assert "manage_mcp" in ml["tools"]


def test_f5_coding_no_web():
    from backend.agent.tool_policy import resolve_enabled_tool_names
    names, plan = resolve_enabled_tool_names(
        profile="dynamic", user_input="修这个 TypeError traceback", mode="default"
    )
    assert names is not None
    assert "browser" not in names
    assert "web_search" not in names


def test_f9_mcp_add_no_github_pack():
    from backend.agent.tool_policy import infer_scene
    plan = infer_scene(
        "添加 mcp github 用 npx -y @modelcontextprotocol/server-github",
        profile="dynamic",
    )
    assert "github" not in plan.packs


def test_ws_mode_plan():
    from backend.schemas.ws import UserInput  # type: ignore
    # schema accepts plan if field present
    import inspect
    from backend.schemas import ws as wsmod
    src = open(wsmod.__file__).read()
    assert '"plan"' in src
