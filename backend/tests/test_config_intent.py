"""Config Intent 快路径：检测不误伤普通编码任务。"""

from backend.services.config_intent import detect_config_intent
from backend.services.secret_redact import redact_secrets
from backend.agent.loop_cluster import LoopClusterMixin


def test_detect_mcp_doubao_key():
    m = detect_config_intent(
        "我给你加了豆包搜索MCP，你配下api   IWimbUB9VSgFwoNpUmmTHeZZmrvMrarR"
    )
    assert m is not None
    assert m.kind == "mcp_key"
    assert m.payload["api_key"].startswith("IWim")


def test_detect_proxy_ipv4():
    m = detect_config_intent("设置代理 127.0.0.1:3128")
    assert m is not None
    assert m.kind == "proxy"
    assert m.payload["host"] == "127.0.0.1"
    assert m.payload["port"] == 3128


def test_detect_simple_mode():
    assert detect_config_intent("开启简单模式").kind == "simple_mode"
    assert detect_config_intent("关闭简单模式").payload["enabled"] is False


def test_no_false_positive_coding():
    assert detect_config_intent("帮我写一个排序算法") is None


def test_redact_key_assign():
    out = redact_secrets("key=IWimbUB9VSgFwoNpUmmTHeZZmrvMrarR")
    assert "IWimbUB9VSgFwoNpUmmTHeZZmrvMrarR" not in out
    assert "…" in out or "***" in out


def test_clamp_min():
    out = LoopClusterMixin._clamp_tool_args(
        None,
        {"properties": {"max_results": {"type": "integer", "minimum": 5, "maximum": 20}}},
        {"max_results": 3},
    )
    assert out["max_results"] == 5


def test_detect_mcp_setup_without_key():
    from backend.services.config_intent import detect_config_intent
    m = detect_config_intent("帮我配一下豆包搜索 MCP")
    assert m is not None
    assert m.kind == "mcp_setup_guide"


def test_generic_configure_mcp_not_doubao():
    from backend.services.config_intent import detect_config_intent
    m = detect_config_intent("帮我配置一下 mcp")
    assert m is None or m.kind != "mcp_setup_guide"


def test_pending_api_key_form():
    from backend.services.config_intent import try_pending_mcp_key
    m = try_pending_mcp_key("API Key：IWimbUB9VSgFwoNpUmmTHeZZmrvMrarR", "tavily")
    assert m is not None
    assert m.payload["label"] == "tavily"


def test_coerce_integer_string_before_validate():
    mixin = LoopClusterMixin()
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "num_results": {"type": "integer", "minimum": 1, "maximum": 15},
        },
        "required": ["query"],
    }
    out = mixin._validate_tool_args(schema, {"query": "tevarn", "num_results": "3"})
    assert out["num_results"] == 3
    assert isinstance(out["num_results"], int)


def test_coerce_then_clamp_integer():
    mixin = LoopClusterMixin()
    schema = {
        "type": "object",
        "properties": {
            "max_results": {"type": "integer", "minimum": 5, "maximum": 20},
        },
    }
    out = mixin._validate_tool_args(schema, {"max_results": "3"})
    assert out["max_results"] == 5


def test_configure_tevarn_topic_status_not_overview():
    import asyncio
    from unittest.mock import AsyncMock, patch

    from backend.skills.builtins.configure_tevarn_skill import ConfigureTevarnSkill

    with patch.object(
        ConfigureTevarnSkill,
        "_status",
        new=AsyncMock(return_value="【Tevarn 系统状态】\nok"),
    ):
        out = asyncio.run(ConfigureTevarnSkill().execute(topic="status"))
    assert "【Tevarn 系统状态】" in out
    assert "总览" not in out
