"""Phase 0.5.2 W2-3：EventBus→WS 桥 + 交互式权限确认 测试

覆盖：
- EventBusWSBridge：run_event 转发 / 无 session_id 跳过 / start-stop 幂等
- 交互式 approval：批准放行 / 拒绝拦截 / 通道异常保守拒绝
  - WAITING↔EXECUTING 状态迁移经 recorder
  - approval.requested / approval.resolved 事件进总线
- 默认 local_allow 模式不受影响（回归）
"""
import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


# ═══════════ 1. EventBusWSBridge ═══════════

class _FakeWSManager:
    def __init__(self):
        self.sent: list[tuple[str, dict]] = []

    async def broadcast(self, session_id, message):
        self.sent.append((str(session_id), message))


def test_bridge_forwards_run_events():
    from backend.core.event_bus import event_bus
    from backend.integrations.event_bus_bridge import EventBusWSBridge

    ws = _FakeWSManager()
    bridge = EventBusWSBridge(ws)
    sid = uuid.uuid4()

    async def _run():
        bridge.start()
        try:
            await event_bus.publish("run.created", {
                "run_id": "r1", "session_id": str(sid), "mode": "default",
            })
            await event_bus.publish("internal.only", {"session_id": str(sid)})  # 不匹配模式
            await event_bus.publish("run.status_changed", {"to": "executing"})  # 无 session_id
            await event_bus.publish("run.completed", {
                "run_id": "r1", "session_id": "not-a-uuid",  # 非法 uuid
            })
        finally:
            bridge.stop()

    asyncio.run(_run())
    assert len(ws.sent) == 1
    sent_sid, msg = ws.sent[0]
    assert sent_sid == str(sid)
    assert msg["type"] == "run_event"
    assert msg["topic"] == "run.created"
    assert msg["data"]["run_id"] == "r1"
    assert not bridge.running


def test_bridge_start_idempotent():
    from backend.integrations.event_bus_bridge import EventBusWSBridge

    bridge = EventBusWSBridge(_FakeWSManager())
    bridge.start()
    n1 = len(bridge._unsubs)
    bridge.start()  # 重复 start 不叠加
    assert len(bridge._unsubs) == n1
    bridge.stop()
    bridge.stop()  # 重复 stop 安全
    assert not bridge.running


# ═══════════ 2. 交互式 approval ═══════════

def _fake_gate(decision: str):
    return SimpleNamespace(
        check=lambda name, args: decision,
        summarize=lambda name, args: f"{name} danger-op",
    )


def _recorder():
    rc = SimpleNamespace()
    rc.run_id = uuid.uuid4()
    rc.transition = AsyncMock(return_value=True)
    return rc


def _interactive_args(recorder):
    return {
        "_session_id": str(uuid.uuid4()),
        "_run_recorder": recorder,
        "command": "rm -rf /tmp/x",
    }


def _patch_gate(decision):
    """把 hook 内部构造的 PermissionGate 换成假 gate"""
    return patch(
        "backend.agent.permissions_rules.PermissionGate",
        return_value=_fake_gate(decision),
    )


class _AutoAnswerWS:
    """收到 confirm_request 后立即按预设答案 resolve"""

    def __init__(self, approved: bool):
        self.approved = approved
        self.requests: list[dict] = []

    async def broadcast(self, session_id, message):
        if message.get("type") == "confirm_request":
            self.requests.append(message)
            from backend.services import confirm_manager

            confirm_manager.resolve_confirmation(
                message["confirm_id"], self.approved
            )


def _run_hook(arguments, ask_mode="interactive"):
    from backend.agent.tool_hooks import builtin_permission_before
    from backend.core.config import settings

    old_mode = settings.agent_permission_ask_mode
    settings.agent_permission_ask_mode = ask_mode
    try:
        with _patch_gate("ask"):
            return asyncio.run(
                builtin_permission_before("shell", arguments)
            )
    finally:
        settings.agent_permission_ask_mode = old_mode


def test_interactive_approval_allowed():
    from backend.core.event_bus import event_bus

    events: list[str] = []

    async def capture(topic, payload):
        events.append(topic)

    rc = _recorder()
    ws = _AutoAnswerWS(approved=True)
    args = _interactive_args(rc)
    args["_ws_manager"] = ws

    unsub = event_bus.subscribe("approval.*", capture)
    try:
        result = _run_hook(args)
    finally:
        unsub()

    assert not result.block  # 用户批准 → 放行
    assert len(ws.requests) == 1
    # WAITING → EXECUTING 迁移
    states = [c.args[0] for c in rc.transition.call_args_list]
    assert states == ["waiting", "executing"]
    # 事件流
    assert "approval.requested" in events
    assert "approval.resolved" in events


def test_interactive_approval_denied():
    rc = _recorder()
    ws = _AutoAnswerWS(approved=False)
    args = _interactive_args(rc)
    args["_ws_manager"] = ws

    result = _run_hook(args)

    assert result.block
    assert "denied by user" in (result.reason or "")
    states = [c.args[0] for c in rc.transition.call_args_list]
    assert states == ["waiting", "executing"]


def test_interactive_approval_channel_error_conservative_deny():
    rc = _recorder()
    args = _interactive_args(rc)
    args["_ws_manager"] = _AutoAnswerWS(approved=True)

    with patch(
        "backend.services.confirm_manager.request_confirmation",
        new=AsyncMock(side_effect=RuntimeError("ws exploded")),
    ):
        result = _run_hook(args)

    assert result.block  # 通道异常 → 保守拒绝
    assert "保守拒绝" in (result.reason or "")


def test_default_local_allow_unchanged():
    """默认 local_allow：不弹确认直接放行（回归保护）"""
    rc = _recorder()
    args = _interactive_args(rc)
    args["_ws_manager"] = _AutoAnswerWS(approved=False)

    result = _run_hook(args, ask_mode="local_allow")

    assert not result.block
    rc.transition.assert_not_called()  # 不进 WAITING
