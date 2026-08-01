"""current_time / python / result_load platform fixes (Windows-safe)."""

from __future__ import annotations

import asyncio

from backend.services.tools.executors import execute_python
from backend.skills.builtins.current_time_skill import CurrentTimeSkill, _resolve_tz


def test_current_time_utc_and_shanghai():
    for z in ("UTC", "Asia/Shanghai", "America/New_York"):
        tz, name, note = _resolve_tz(z)
        assert tz is not None
        text = asyncio.run(CurrentTimeSkill().execute(timezone=z))
        assert "本地时间:" in text
        assert "UTC:" in text


def test_python_tool_multiline():
    out = asyncio.run(execute_python({}, {"code": "print(40+2)\nprint('ok')"}))
    assert "42" in out
    assert "ok" in out


def test_result_load_tool_registered():
    from backend.agent.tool_policy import DEFAULT_CHAT_TOOL_WHITELIST
    from backend.tools.builtins.core_tools import BUILTIN_TOOL_CLASSES, ResultLoadTool

    assert any(
        c is ResultLoadTool or getattr(c, "__name__", "") == "ResultLoadTool"
        for c in BUILTIN_TOOL_CLASSES
    )
    assert "result_load" in DEFAULT_CHAT_TOOL_WHITELIST
    tool = ResultLoadTool()
    assert tool.name == "result_load"
    msg = asyncio.run(tool.execute())
    assert "required" in msg.lower() or "Error" in msg
