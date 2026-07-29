"""权限门必须 fail-closed。

审计发现有三层 fail-open 叠在一起：

  1. tool_hooks.run_before_tool_call —— 单个 handler 抛异常只记 warning 后继续
  2. tool_hooks.builtin_permission_before —— catch-all except 后 return 放行
  3. tools.registry.execute —— hook 机制抛异常后没有 return，继续执行工具

任何一层触发，整套权限规则就静默消失，而日志只有一行 DEBUG/WARNING。
对本地优先产品来说这尤其要命：Agent 面对的是用户真实的文件系统。
"""

import pytest

from backend.agent import tool_hooks
from backend.agent.tool_hooks import (
    BeforeHookResult,
    clear_tool_hooks,
    register_before_tool_call,
    run_before_tool_call,
)


@pytest.fixture(autouse=True)
def _clean_hooks():
    clear_tool_hooks()
    yield
    clear_tool_hooks()


# ── 第 1 层：critical handler 异常 = 拒绝 ────────────────────


@pytest.mark.asyncio
async def test_critical_handler_exception_blocks():
    async def _explodes(name, args):
        raise RuntimeError("boom")

    register_before_tool_call(_explodes, critical=True)
    res = await run_before_tool_call("command", {"command": "rm -rf /"})
    assert res.block is True, "安全关键 handler 崩了必须拦住调用"
    assert "权限检查未能完成" in res.reason
    assert "RuntimeError" in res.reason, "要告诉用户到底出了什么错"


@pytest.mark.asyncio
async def test_non_critical_handler_exception_does_not_block():
    """文件快照 / 历史记录失败不该阻断用户工作 —— 只有安全 handler 才 fail-closed。"""
    called = {"n": 0}

    async def _snapshot_fails(name, args):
        raise OSError("disk full")

    async def _later(name, args):
        called["n"] += 1
        return BeforeHookResult(arguments=args)

    register_before_tool_call(_snapshot_fails)  # critical=False
    register_before_tool_call(_later)
    res = await run_before_tool_call("file_write", {"filepath": "a"})
    assert res.block is False
    assert called["n"] == 1, "非关键 handler 失败后应继续执行后续 handler"


@pytest.mark.asyncio
async def test_builtin_permission_hook_is_registered_as_critical():
    """真正的权限 handler 必须挂上 critical 标记，否则上面那层白写。"""
    tool_hooks.ensure_builtin_hooks_registered()
    assert tool_hooks.builtin_permission_before in tool_hooks._critical_before
    # 快照 / 历史不是安全边界，不该是 critical
    assert tool_hooks.builtin_write_checkpoint_before not in tool_hooks._critical_before
    assert tool_hooks.builtin_file_history_before not in tool_hooks._critical_before


# ── 第 2 层：权限判定本身出错 = 拒绝 ─────────────────────────


@pytest.mark.asyncio
async def test_permission_decision_error_blocks(monkeypatch):
    """gate.check 抛异常时不得放行。"""
    from backend.agent import permissions_rules

    def _broken_check(self, tool, arguments=None):
        raise ValueError("rules corrupted")

    monkeypatch.setattr(permissions_rules.PermissionGate, "check", _broken_check)
    tool_hooks.ensure_builtin_hooks_registered()

    res = await run_before_tool_call("command", {"command": "rm -rf ~"})
    assert res.block is True, "权限判定出错时放行 = 整套规则形同虚设"


@pytest.mark.asyncio
async def test_explicitly_disabled_permissions_still_allow(monkeypatch):
    """用户显式关掉权限系统是明确意图，不属于「出错」，应放行。"""
    from backend.core.config import settings

    monkeypatch.setattr(settings, "agent_permission_enabled", False, raising=False)
    tool_hooks.ensure_builtin_hooks_registered()
    res = await run_before_tool_call("command", {"command": "rm -rf /"})
    assert res.block is False


# ── 第 3 层：registry 层的 hook 机制故障 = 拒绝 ──────────────


@pytest.mark.asyncio
async def test_registry_blocks_when_hook_machinery_fails(monkeypatch):
    """run_before_tool_call 本身抛异常时，registry 不能继续执行工具。"""
    from backend.tools.base import BaseTool
    from backend.tools.registry import ToolRegistry

    executed = {"n": 0}

    class _Danger(BaseTool):
        def __init__(self):
            super().__init__(name="_danger_probe", description="d", parameters={})

        async def execute(self, **kw):
            executed["n"] += 1
            return "executed!"

    async def _boom(name, args):
        raise ImportError("permissions module missing")

    monkeypatch.setattr(tool_hooks, "run_before_tool_call", _boom)
    ToolRegistry.register(_Danger())
    try:
        out = await ToolRegistry.execute("_danger_probe", {})
    finally:
        ToolRegistry.unregister("_danger_probe")

    assert executed["n"] == 0, "权限检查没跑成，工具绝不能执行"
    assert "Security Blocked" in str(out)
    assert "ImportError" in str(out)
