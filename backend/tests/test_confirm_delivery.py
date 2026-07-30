"""危险操作确认必须真的送到人眼前 —— 回归测试。

背景（2026-07 审计发现）：COMMAND_CATEGORIES 八类的默认动作是 confirm，
但确认弹窗从未出现过。链路：

    tool_round 注入 _session_id
      → registry.execute 的 _DROP_META 把 _session_id 剥掉
      → executors.execute_command 拿到 session_id=None
      → ConnectionManager.broadcast(None, ...) 对未知 session 静默 return
      → request_confirmation 干等 30s 超时
      → 返回「[Denied] rejected by user」——用户根本没被问过

三个独立缺陷叠加，任何一个被堵住都不会有这个后果。下面逐个钉死。
"""

import asyncio
import uuid

import pytest

from backend.services import confirm_manager
from backend.services.confirm_manager import ConfirmOutcome


class _FakeWS:
    """模拟 CONNECTED 状态的 WebSocket。"""

    class _State:
        value = 1

    client_state = _State()


class _FakeManager:
    """够用的 ConnectionManager 替身：记录 broadcast 收到的 session_id。"""

    def __init__(self, connected_sid=None):
        self._connections = {connected_sid: _FakeWS()} if connected_sid else {}
        self.sent = []

    def is_connected(self, session_id) -> bool:
        if session_id is None:
            return False
        sid = session_id
        if isinstance(session_id, str):
            try:
                sid = uuid.UUID(session_id)
            except (ValueError, AttributeError):
                return False
        return sid in self._connections

    async def broadcast(self, session_id, message):
        self.sent.append((session_id, message))


# ── 缺陷 1：_session_id 必须穿透到执行器 ─────────────────────


def test_drop_meta_keeps_session_id():
    """registry 不得剥离 _session_id —— 执行器的确认流程靠它路由。"""
    import inspect

    from backend.tools.registry import ToolRegistry

    src = inspect.getsource(ToolRegistry.execute)
    drop_line = next(l for l in src.splitlines() if "_DROP_META = {" in l)
    assert "_session_id" not in drop_line, (
        "_session_id 被剥离会导致 broadcast(None) 静默失败，确认弹窗永不送达"
    )
    # _ws_manager 一直是穿透的，两者必须同进同出
    assert "_ws_manager" not in drop_line


@pytest.mark.asyncio
async def test_session_id_reaches_executor():
    """端到端：走 ToolRegistry.execute，执行器应能看到 _session_id。"""
    from backend.tools.base import BaseTool
    from backend.tools.registry import ToolRegistry

    seen = {}

    class _Probe(BaseTool):
        def __init__(self):
            super().__init__(name="_probe_meta", description="d", parameters={})

        async def execute(self, **kw):
            seen.update(kw)
            return "ok"

    ToolRegistry.register(_Probe())
    try:
        sid = uuid.uuid4()
        await ToolRegistry.execute(
            "_probe_meta", {"_session_id": sid, "_ws_manager": object()}
        )
    finally:
        ToolRegistry.unregister("_probe_meta")

    assert seen.get("_session_id") == sid, "执行器拿不到 _session_id，确认无法路由"


# ── 缺陷 2：送不到就别空等，且要如实上报 ──────────────────


@pytest.mark.asyncio
async def test_no_live_connection_returns_immediately_and_is_honest():
    """没人在听时立即返回，不占满超时；且 reason 不能是 denied。"""
    mgr = _FakeManager(connected_sid=None)

    import time as _time

    started = _time.monotonic()
    outcome = await confirm_manager.request_confirmation(
        mgr, uuid.uuid4(), title="t", command="rm -rf /", timeout=30.0
    )
    elapsed = _time.monotonic() - started

    assert outcome.approved is False
    assert outcome.reason == "not_connected"
    assert outcome.asked is False, "没送达就不该算『问过用户』"
    assert "拒绝" not in outcome.describe() or "无法送达" in outcome.describe()
    assert elapsed < 1.0, f"应立即返回而非空等超时，实际 {elapsed:.1f}s"
    assert mgr.sent == [], "明知没人在听就不该再 broadcast"


@pytest.mark.asyncio
async def test_no_channel_is_distinguishable_from_denial():
    """headless（无 ws_manager）与「用户点了拒绝」必须能区分开。"""
    no_channel = await confirm_manager.request_confirmation(
        None, uuid.uuid4(), title="t", command="rm -rf /"
    )
    assert no_channel.reason == "no_channel"
    assert no_channel.asked is False

    denied = ConfirmOutcome(False, "denied")
    assert denied.asked is True
    # 两者都是 falsy，但语义不同 —— 工具层据此给出不同措辞
    assert not no_channel and not denied
    assert no_channel.describe() != denied.describe()


@pytest.mark.asyncio
async def test_approval_round_trip():
    """连接正常时：请求送达 → 用户批准 → outcome 为真。"""
    sid = uuid.uuid4()
    mgr = _FakeManager(connected_sid=sid)

    task = asyncio.create_task(
        confirm_manager.request_confirmation(
            mgr, sid, title="t", command="rm -rf build", timeout=5.0
        )
    )
    # 等 broadcast 发出，取回 confirm_id 再回执
    for _ in range(50):
        await asyncio.sleep(0.01)
        if mgr.sent:
            break
    assert mgr.sent, "确认请求未推送"
    confirm_id = mgr.sent[0][1]["confirm_id"]
    assert confirm_manager.resolve_confirmation(confirm_id, True) is True

    outcome = await task
    assert outcome.approved is True
    assert outcome.reason == "approved"


# ── 缺陷 3：broadcast 对未知 session 是静默的，调用方必须先问 ──


def test_connection_manager_exposes_is_connected():
    """confirm_manager 依赖这个探测；ConnectionManager 必须提供它。"""
    from backend.api.websocket import ConnectionManager

    mgr = ConnectionManager()
    assert hasattr(mgr, "is_connected")
    assert mgr.is_connected(None) is False
    assert mgr.is_connected(uuid.uuid4()) is False
    assert mgr.is_connected("not-a-uuid") is False
