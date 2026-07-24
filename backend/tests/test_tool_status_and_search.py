from backend.agent.tool_status import format_tool_status
from backend.services.tools.web_search_unified import resolve_tavily_api_key


def test_format_search_status():
    s = format_tool_status("search", {"query": "python asyncio best practices"})
    assert "search" in s
    assert "asyncio" in s


def test_format_file_read():
    s = format_tool_status("file_read", {"filepath": "src/main.py"})
    assert "file_read" in s
    assert "main.py" in s


def test_resolve_tavily_env(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key")
    assert resolve_tavily_api_key() == "tvly-test-key"
