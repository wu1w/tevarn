"""本员工允许后不应反复弹 command 确认。"""

from __future__ import annotations

import asyncio

import pytest

from backend.agent.grant_store import (
    add_session_grant,
    allow_signature,
    has_identity_tool_grant,
    has_session_grant,
    reset_for_tests,
    tool_matches_crew_caps,
)


@pytest.fixture(autouse=True)
def _clear_grants():
    reset_for_tests()
    yield
    reset_for_tests()


def test_tool_matches_command_cap():
    assert tool_matches_crew_caps("command", ["command", "file_rw"])
    assert tool_matches_crew_caps("python", ["command"])
    assert not tool_matches_crew_caps("command", ["file_rw"])
    assert tool_matches_crew_caps("configure_tevarn", ["manage_skill"])
    assert tool_matches_crew_caps("get_system_status", ["current_time"])


def test_session_whole_tool_covers_any_command_head():
    sid = "sess-1"
    add_session_grant(sid, "command", {"command": "rm -rf build"}, whole_tool=True)
    assert has_session_grant(sid, "command", {"command": "rm -rf build"})
    # 不同命令首词也应放行（本员工允许后的会话缓存）
    assert has_session_grant(sid, "command", {"command": "npm install"})
    assert has_session_grant(sid, "command", {"command": "curl http://x"})


def test_session_head_only_without_whole_tool():
    sid = "sess-2"
    add_session_grant(sid, "command", {"command": "rm -rf build"}, whole_tool=False)
    assert has_session_grant(sid, "command", {"command": "rm foo"})
    assert not has_session_grant(sid, "command", {"command": "npm install"})
    assert allow_signature("command", {"command": "/usr/bin/rm x"}) == "command:rm"


def test_identity_tool_grant_from_caps_list():
    async def go():
        ok = await has_identity_tool_grant(
            "command",
            capabilities=["file_rw", "command"],
        )
        assert ok is True
        no = await has_identity_tool_grant(
            "command",
            capabilities=["file_rw"],
        )
        assert no is False

    asyncio.run(go())


def test_permission_before_identity_cap_skips_ask(monkeypatch):
    """builtin_permission_before：有 command 编制能力时 ask→allow。"""

    async def go():
        from backend.agent import tool_hooks
        from backend.agent.permissions_rules import PermissionGate
        from backend.kernel.permission_court import CourtDecision

        # 模拟 Rust/court 对 command 恒 ask（真实路径）
        async def _ask_court(name, arguments=None, **kw):
            return CourtDecision(
                tool=name,
                args_digest="x",
                verdict="ask",
                matched_rule="profile:confirm",
                layer="profile",
                reason="test",
            )

        monkeypatch.setattr(
            "backend.kernel.permission_court.decide_tool",
            _ask_court,
        )
        monkeypatch.setattr(
            PermissionGate,
            "check",
            lambda self, tool, args=None: "ask",
        )
        monkeypatch.setattr(
            "backend.agent.working_mode.effective_permission_profile",
            lambda: "cautious",
        )
        monkeypatch.setattr(
            "backend.agent.working_mode.effective_ask_mode",
            lambda: "interactive",
        )
        monkeypatch.setattr(
            "backend.core.config.settings.agent_permission_enabled",
            True,
            raising=False,
        )

        # 非 workforce；带编制能力
        args = {
            "_session_id": "s1",
            "_identity_id": "ident-1",
            "_identity_capabilities": ["command", "file_rw"],
            "_ws_manager": object(),  # 有通道也不该弹
            "command": "npm test",
        }

        # 不走 workforce
        monkeypatch.setattr(
            "backend.agent.steward_permission.is_workforce_context",
            lambda *a, **k: False,
        )

        interactive_called = {"n": 0}

        async def _no_interactive(*a, **k):
            interactive_called["n"] += 1
            raise AssertionError("should not open interactive dialog")

        monkeypatch.setattr(tool_hooks, "_interactive_approval", _no_interactive)

        result = await tool_hooks.builtin_permission_before("command", args)
        assert not result.block
        assert result.arguments.get("_confirm_ok") is True
        assert interactive_called["n"] == 0

    asyncio.run(go())


def test_permission_before_session_grant_skips_ask(monkeypatch):
    """本会话允许后：court 再 ask 也不得弹窗（含不同 command 首词）。"""

    async def go():
        from backend.agent import tool_hooks
        from backend.kernel.permission_court import CourtDecision

        async def _ask_court(name, arguments=None, **kw):
            return CourtDecision(
                tool=name,
                args_digest="x",
                verdict="ask",
                matched_rule="profile:confirm",
                layer="profile",
                reason="test",
            )

        monkeypatch.setattr(
            "backend.kernel.permission_court.decide_tool",
            _ask_court,
        )
        monkeypatch.setattr(
            "backend.core.config.settings.agent_permission_enabled",
            True,
            raising=False,
        )
        monkeypatch.setattr(
            "backend.agent.steward_permission.is_workforce_context",
            lambda *a, **k: False,
        )

        sid = "sess-grant-1"
        # 模拟 UI「本会话允许」：整工具
        add_session_grant(
            sid, "command", {"command": "cmd /c dir"}, whole_tool=True
        )

        interactive_called = {"n": 0}

        async def _no_interactive(*a, **k):
            interactive_called["n"] += 1
            raise AssertionError("should not open interactive dialog")

        monkeypatch.setattr(tool_hooks, "_interactive_approval", _no_interactive)

        # 相同首词
        r1 = await tool_hooks.builtin_permission_before(
            "command",
            {"_session_id": sid, "command": "cmd /c echo 1"},
        )
        assert not r1.block
        assert r1.arguments.get("_confirm_ok") is True
        # 不同首词也应放行
        r2 = await tool_hooks.builtin_permission_before(
            "command",
            {"_session_id": sid, "command": "npm test"},
        )
        assert not r2.block
        assert r2.arguments.get("_confirm_ok") is True
        assert interactive_called["n"] == 0

    asyncio.run(go())
