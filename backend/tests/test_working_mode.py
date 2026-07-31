"""T5：工作方式 / 执行环境 —— 权限体系的单一事实源。

此前的核心问题不是「规则写得不对」，而是：
  - agent_permission_ask_mode 默认 local_allow，把所有 ask 静默降级为放行
  - requires_confirmation 声明了但全项目无人读取
  - 沙箱默认关闭，实际边界只剩一条可轻易绕过的正则黑名单
本文件锁定修复后的语义。
"""

import pytest

from backend.agent import working_mode as wm
from backend.agent.tool_hooks import builtin_permission_before
from backend.core.config import settings
from backend.services.confirm_manager import ConfirmOutcome


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    """每个用例从出厂默认出发，避免互相污染。"""
    monkeypatch.setattr(settings, "agent_working_mode", "cautious", raising=False)
    monkeypatch.setattr(settings, "agent_execution_mode", "auto", raising=False)
    monkeypatch.setattr(settings, "agent_permission_profile", "auto", raising=False)
    monkeypatch.setattr(settings, "agent_permission_ask_mode", "auto", raising=False)
    monkeypatch.setattr(settings, "agent_permission_headless", "safe", raising=False)
    monkeypatch.setattr(settings, "agent_permission_enabled", True, raising=False)
    monkeypatch.setattr(settings, "agent_computer_enabled", False, raising=False)


def _cap(mode, available, label="x", level="full"):
    from backend.computer.detect import SandboxCapability

    return SandboxCapability(mode, level, available, label)


def _patch_cap(monkeypatch, cap):
    monkeypatch.setattr(
        "backend.computer.detect.detect_sandbox_capability",
        lambda platform=None: cap,
    )


# ── 默认值：不再是「本机直跑 + 全放行」 ──────────────────────


def test_defaults_are_secure():
    assert wm.resolve_working_mode().id == "cautious"
    assert wm.effective_permission_profile() == "cautious"
    # 关键：默认不再是 local_allow（那会让整套规则形同虚设）
    assert wm.effective_ask_mode() == "auto"


def test_default_execution_prefers_sandbox_when_available(monkeypatch):
    _patch_cap(monkeypatch, _cap("bwrap", True))
    d = wm.decide_sandbox()
    assert d.use_sandbox is True
    assert d.degraded is False


# ── 三档执行环境语义各不相同 ────────────────────────────────


def test_auto_degrades_visibly_without_sandbox(monkeypatch):
    """auto 无沙箱时退回本机，但必须打上 degraded 让 UI 说明白。"""
    _patch_cap(monkeypatch, _cap("none", False, level="none"))
    d = wm.decide_sandbox()
    assert d.use_sandbox is False
    assert d.degraded is True
    assert d.reason


def test_sandbox_mode_never_silently_falls_back(monkeypatch):
    """强制沙箱：不可用时仍返回 use_sandbox=True，由执行层报错。

    静默降级到本机直跑会彻底破坏用户预期 —— 用户以为隔离着，其实没有。
    """
    monkeypatch.setattr(settings, "agent_execution_mode", "sandbox", raising=False)
    _patch_cap(monkeypatch, _cap("none", False, level="none"))
    d = wm.decide_sandbox()
    assert d.use_sandbox is True
    assert d.degraded is False


def test_local_mode_is_explicit(monkeypatch):
    monkeypatch.setattr(settings, "agent_execution_mode", "local", raising=False)
    _patch_cap(monkeypatch, _cap("bwrap", True))
    assert wm.decide_sandbox().use_sandbox is False


def test_local_mode_overrides_legacy_computer_enabled(monkeypatch):
    """显式 execution_mode=local 优先于旧 agent_computer_enabled。

    升级路径：旧 computer_enabled=True 应映射到 auto/sandbox，而不是
    与 local 并存时再强行隔离——用户写 local 就是要本机。
    """
    from backend.services.tools.executors import should_use_sandbox

    monkeypatch.setattr(settings, "agent_execution_mode", "local", raising=False)
    monkeypatch.setattr(settings, "agent_computer_enabled", True, raising=False)
    assert should_use_sandbox() is False


def test_legacy_computer_enabled_in_auto_prefers_sandbox(monkeypatch):
    """auto + computer_enabled=True：有沙箱能力则使用（旧「优先隔离」语义）。"""
    from backend.services.tools.executors import should_use_sandbox

    monkeypatch.setattr(settings, "agent_execution_mode", "auto", raising=False)
    monkeypatch.setattr(settings, "agent_computer_enabled", True, raising=False)
    _patch_cap(monkeypatch, _cap("bwrap", True))
    assert should_use_sandbox() is True


def test_invalid_values_fall_back_to_safe_defaults(monkeypatch):
    monkeypatch.setattr(settings, "agent_working_mode", "yolo-max", raising=False)
    monkeypatch.setattr(settings, "agent_execution_mode", "nonsense", raising=False)
    # 非法值绝不能放宽权限（默认执行模式为 sandbox，见 DEFAULT_EXECUTION_MODE）
    assert wm.resolve_working_mode().id == "cautious"
    assert wm.resolve_execution_mode() == "sandbox"


# ── 工作方式 → profile 映射 ─────────────────────────────────


@pytest.mark.parametrize(
    "mode,profile",
    [
        ("readonly", "plan"),
        ("cautious", "cautious"),
        ("auto_edit", "acceptEdits"),
        ("autonomous", "free"),
    ],
)
def test_working_mode_maps_to_profile(monkeypatch, mode, profile):
    monkeypatch.setattr(settings, "agent_working_mode", mode, raising=False)
    assert wm.effective_permission_profile() == profile


def test_explicit_override_wins_and_is_surfaced(monkeypatch):
    """高级用户覆盖底层键时，describe_current 必须标出来，
    否则 UI 显示的工作方式与实际行为不符且无从察觉。"""
    monkeypatch.setattr(settings, "agent_working_mode", "cautious", raising=False)
    monkeypatch.setattr(settings, "agent_permission_profile", "free", raising=False)
    assert wm.effective_permission_profile() == "free"
    d = wm.describe_current()
    assert d["overrides"]["permission_profile"] == "free"
    assert d["working_mode"] == "cautious"


def test_describe_marks_sandbox_option_unavailable(monkeypatch):
    """本机无沙箱时「强制沙箱」选项要能置灰。"""
    _patch_cap(monkeypatch, _cap("none", False, level="none"))
    opts = {m["id"]: m for m in wm.describe_current()["execution_modes"]}
    assert opts["sandbox"]["available"] is False
    assert opts["auto"]["available"] is True


# ── ask 分支：auto 的通道感知 ───────────────────────────────


@pytest.mark.asyncio
async def test_ask_prompts_when_approval_channel_exists(monkeypatch):
    """有 WS 通道 → 真弹窗确认（而非静默放行）。"""
    seen = {}

    async def _fake(ws, session_id, **kw):
        seen["asked"] = True
        return ConfirmOutcome(False, "denied")  # 用户拒绝

    monkeypatch.setattr(
        "backend.services.confirm_manager.request_confirmation", _fake
    )
    res = await builtin_permission_before(
        "command",
        {"command": "rm -rf build", "_ws_manager": object(), "_session_id": "s1"},
    )
    assert seen.get("asked") is True
    assert res.block is True


@pytest.mark.asyncio
async def test_headless_default_blocks_shell_but_not_file_work(monkeypatch):
    """无人值守兜底默认 safe：读写照常，能跑任意代码的一类拒绝。

    这条路径没人可问，而它正是外部内容（邮件 / 群消息 / webhook）进入本机的
    入口 —— 提示词注入走的就是这里。旧默认 allow 等于权限规则在这条路上不存在；
    一刀切 deny 又会静默弄坏「整理笔记 / 汇总报告」这类正常定时任务。
    """
    shell = await builtin_permission_before("command", {"command": "pytest -q"})
    assert shell.block is True, "无人值守下 shell 必须拒绝"

    write = await builtin_permission_before(
        "file_write", {"filepath": "notes/daily.md", "content": "x"}
    )
    assert write.block is False, "写文件是定时任务的常规操作，不该被拦"

    read = await builtin_permission_before("file_read", {"filepath": "notes/daily.md"})
    assert read.block is False


@pytest.mark.asyncio
async def test_headless_can_be_loosened_to_allow(monkeypatch):
    """完全信任所有触发源的人可以显式退回旧行为。"""
    monkeypatch.setattr(settings, "agent_permission_headless", "allow", raising=False)
    res = await builtin_permission_before("command", {"command": "pytest -q"})
    assert res.block is False


@pytest.mark.asyncio
async def test_headless_can_be_tightened_to_deny(monkeypatch):
    """deny = 规则说「问用户」而无人可问时一律拒绝。

    注意它只作用于 ask 分支：规则直接判 allow 的操作（cautious 下的 file_write）
    仍然放行。要连写也禁掉，应该换工作方式（readonly），而不是调这个兜底 ——
    两者是正交的，混在一起会让「工作方式」失去意义。
    """
    monkeypatch.setattr(settings, "agent_permission_headless", "deny", raising=False)
    res = await builtin_permission_before("command", {"command": "pytest -q"})
    assert res.block is True

    res = await builtin_permission_before("file_write", {"filepath": "a", "content": "x"})
    assert res.block is False, "cautious 规则本就允许工作区编辑，与 headless 兜底无关"

    # 想连写一起禁：改工作方式
    monkeypatch.setattr(settings, "agent_working_mode", "readonly", raising=False)
    res = await builtin_permission_before("file_write", {"filepath": "a", "content": "x"})
    assert res.block is True


@pytest.mark.asyncio
async def test_readonly_mode_blocks_writes(monkeypatch):
    monkeypatch.setattr(settings, "agent_working_mode", "readonly", raising=False)
    res = await builtin_permission_before(
        "file_write", {"filepath": "a.py", "content": "x"}
    )
    assert res.block is True


@pytest.mark.asyncio
async def test_auto_edit_lets_edits_through_without_asking(monkeypatch):
    asked = {"n": 0}

    async def _fake(*a, **kw):
        asked["n"] += 1
        return ConfirmOutcome(True, "approved")

    monkeypatch.setattr(
        "backend.services.confirm_manager.request_confirmation", _fake
    )
    monkeypatch.setattr(settings, "agent_working_mode", "auto_edit", raising=False)
    res = await builtin_permission_before(
        "file_write",
        {"filepath": "a.py", "content": "x", "_ws_manager": object()},
    )
    assert res.block is False
    assert asked["n"] == 0, "acceptEdits 语义就是编辑不打扰，不该弹窗"


# ── requires_confirmation：从死标志变成真语义 ────────────────


@pytest.mark.asyncio
async def test_custom_tool_self_declared_confirmation_is_honoured(monkeypatch):
    """规则未覆盖的自定义/MCP 工具，其 requires_confirmation 现在真的生效。"""
    from backend.tools.base import BaseTool, ToolRiskLevel

    class _Custom(BaseTool):
        def __init__(self):
            super().__init__(
                name="my_custom_danger",
                description="d",
                parameters={},
                risk_level=ToolRiskLevel.HIGH,
                requires_confirmation=True,
            )

        async def execute(self, **kw):
            return "ok"

    monkeypatch.setattr(
        "backend.tools.registry.ToolRegistry.get",
        classmethod(lambda cls, n: _Custom() if n == "my_custom_danger" else None),
    )
    asked = {"n": 0}

    async def _fake(*a, **kw):
        asked["n"] += 1
        return ConfirmOutcome(False, "denied")

    monkeypatch.setattr(
        "backend.services.confirm_manager.request_confirmation", _fake
    )

    res = await builtin_permission_before(
        "my_custom_danger", {"_ws_manager": object()}
    )
    assert asked["n"] == 1
    assert res.block is True


@pytest.mark.asyncio
async def test_self_declaration_cannot_override_profile_intent(monkeypatch):
    """acceptEdits 明确表达「编辑别问我」，不该被 file_write 的自声明推翻。"""
    asked = {"n": 0}

    async def _fake(*a, **kw):
        asked["n"] += 1
        return ConfirmOutcome(True, "approved")

    monkeypatch.setattr(
        "backend.services.confirm_manager.request_confirmation", _fake
    )
    monkeypatch.setattr(settings, "agent_working_mode", "auto_edit", raising=False)
    await builtin_permission_before(
        "file_write", {"filepath": "a.py", "_ws_manager": object()}
    )
    assert asked["n"] == 0


# ── 黑名单是提示，不是边界 ──────────────────────────────────


def test_dangerous_regex_is_trivially_bypassable():
    """记录事实：正则黑名单挡不住拼接/编码，真正的边界是沙箱。

    这条测试不是要求修好黑名单（做不到），而是防止有人把它当成安全依赖。
    """
    from backend.services.tools.executors import _match_dangerous

    assert _match_dangerous("rm -rf /") is not None  # 直白写法能拦
    # 以下均为等价的破坏性命令，黑名单全部漏掉
    assert _match_dangerous("$(printf '\\x72\\x6d') -rf /") is None
    assert _match_dangerous("echo cm0gLXJmIC8K | base64 -d | sh") is None
    assert _match_dangerous("R=rm; $R -rf /") is None
