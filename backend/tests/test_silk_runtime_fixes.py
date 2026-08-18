"""Regression tests for the five chat/runtime silk bugs (0.4.3 follow-up)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_locale_has_streaming_action_keys():
    """t() returns the key when missing; || 中文兜底永远走不到 — keys must exist."""
    needed = (
        "chat.queue",
        "chat.newTask",
        "chat.steer",
        "chat.queueHint",
        "chat.interruptHint",
        "chat.steerApplied",
        "chat.queued",
        "chat.steerPlaceholder",
        "chat.interruptStarting",
        "chat.wsKickedByPeer",
        "chat.reclaimWs",
        "chat.peerStreaming",
        "chat.wsKickedInputDisabled",
        "chat.checkpointRestored",
        "chat.stop",
        "chat.stopping",
        "chat.stopped",
        "chat.resuming",
        "chat.startingQueued",
        "chat.stoppingPrevious",
        "chat.loadingOlder",
        "chat.loadOlder",
        "chat.historyStart",
        "chat.sessionDeleted",
        "chat.historyLoadFailed",
        "chat.retryLoad",
        "chat.duplicateIgnored",
        "chat.toolsMenu",
        "chat.moreActions",
        "chat.inspectorFiles",
        "chat.inspectorPreview",
        "chat.inspectorRun",
        "chat.inspectorTrace",
        "chat.inspectorClose",
        "chat.previewEmpty",
        "chat.contextFiles",
        "chat.contextGoal",
    )
    for name in ("zh.json", "en.json"):
        data = json.loads((ROOT / "frontend" / "locales" / name).read_text(encoding="utf-8"))
        for key in needed:
            assert key in data, f"missing {key} in {name}"
            assert data[key].strip()
            assert data[key] != key


def test_parse_first_ws_frame_replays_sync():
    from backend.api.ws_handshake import parse_first_ws_frame

    auth = parse_first_ws_frame('{"type":"auth","token":"abc"}')
    assert auth.is_auth is True
    assert auth.token == "abc"
    assert auth.pending_raw is None

    empty_auth = parse_first_ws_frame('{"type":"auth"}')
    assert empty_auth.is_auth is True
    assert empty_auth.token == ""

    sync = parse_first_ws_frame('{"type":"sync","last_message_id":null}')
    assert sync.is_auth is False
    assert sync.pending_raw is not None
    assert "sync" in sync.pending_raw

    ping = parse_first_ws_frame('{"type":"ping"}')
    assert ping.is_auth is False
    assert ping.pending_raw is not None

    garbage = parse_first_ws_frame("not-json")
    assert garbage.is_auth is False
    assert garbage.pending_raw == "not-json"


def test_auth_ok_payload_shape():
    from backend.api.ws_handshake import auth_ok_payload

    p = auth_ok_payload("user-1")
    assert p == {"type": "auth_ok", "user_id": "user-1"}
    assert auth_ok_payload(None)["user_id"] is None


def test_duplicate_ack_never_idles_while_agent_running():
    from backend.api.chat_dedup import duplicate_ack_payload

    running = duplicate_ack_payload(agent_running=True)
    assert running["type"] == "user_input_ignored"
    assert running["agent_running"] is True
    assert running.get("state") != "idle"

    idle = duplicate_ack_payload(agent_running=False)
    assert idle["type"] == "user_input_ignored"
    assert idle.get("state") != "idle"
    assert idle["agent_running"] is False


def test_websocket_endpoint_replays_first_frame_and_sends_auth_ok():
    src = (ROOT / "backend" / "api" / "websocket.py").read_text(encoding="utf-8")
    assert "parse_first_ws_frame" in src
    assert "pending_first_raw" in src
    assert "auth_ok_payload" in src
    assert "duplicate_ack_payload" in src
    # Old bug: drop duplicate always broadcast idle
    assert '"state": "idle",\n                                    "detail": "忽略重复发送' not in src
    # Agent crash must idle so the composer unlocks (error toast alone is not enough)
    assert src.count('"detail": "出错了，请再试一次。"') >= 2


def test_chat_ui_ignores_idle_while_agent_running():
    src = (ROOT / "frontend" / "app" / "chat" / "page.tsx").read_text(encoding="utf-8")
    assert "if (msg.agent_running)" in src
    assert "Must not unlock the composer" in src or "ghost" in src.lower()


def test_jwt_fingerprint_and_reuse_predicate():
    from backend.api.runtime_identity import (
        FASTAPI_ROLE,
        can_reuse_detached_backend,
        jwt_fingerprint,
        runtime_status_base,
    )

    secret = "unit-test-jwt-secret"
    fp = jwt_fingerprint(secret)
    assert fp == hashlib.sha256(secret.encode("utf-8")).hexdigest()[:16]
    assert jwt_fingerprint("") == ""

    good = {
        "ok": True,
        "role": FASTAPI_ROLE,
        "product": "tevarn-aios",
        "jwt_fp": fp,
    }
    assert can_reuse_detached_backend(good, fp) is True

    # Dirty JWT: secrets.json rotated, detached process still has old secret
    assert can_reuse_detached_backend(good, jwt_fingerprint("other-secret")) is False

    lying_kernel = dict(good, role="kernel_host")
    assert can_reuse_detached_backend(lying_kernel, fp) is False

    legacy_no_fp = {
        "ok": True,
        "role": "kernel_host",
        "product": "tevarn-aios",
    }
    assert can_reuse_detached_backend(legacy_no_fp, fp) is False

    # product-only match (old Electron matcher) is not enough
    assert can_reuse_detached_backend({"ok": True, "product": "tevarn-aios"}, fp) is False

    base = runtime_status_base()
    assert base["role"] == FASTAPI_ROLE
    assert base["product"] == "tevarn-aios"
    assert "jwt_fp" in base
    assert "pid" in base


def test_electron_single_instance_and_jwt_reuse_guard():
    src = (ROOT / "electron" / "main.ts").read_text(encoding="utf-8")
    assert "requestSingleInstanceLock" in src
    assert "second-instance" in src
    assert "isReusableTevarnBackend" in src
    assert "jwt_fp" in src
    assert "fastapi_backend" in src
    assert "j?.product === 'tevarn-aios' || j?.role === 'kernel_host'" not in src


def test_runtime_status_route_uses_identity_helper():
    src = (ROOT / "backend" / "api" / "routes" / "runtime_status.py").read_text(
        encoding="utf-8"
    )
    assert "runtime_status_base" in src
    assert '"role": "kernel_host"' not in src


@pytest.mark.asyncio
async def test_capability_confirm_no_channel_leaves_escalation():
    from backend.agent.kernel_escalation_ui import offer_kernel_capability_confirm

    class K:
        def __init__(self):
            self.approved = []
            self.denied = []

        async def request_escalation(self, pid, caps, *, reason=""):
            return SimpleNamespace(id="esc-no-fe")

        async def approve_escalation(self, rid, *, by="user"):
            self.approved.append(rid)

        async def deny_escalation(self, rid, *, by="user"):
            self.denied.append(rid)

    k = K()
    result = await offer_kernel_capability_confirm(
        kernel=k,
        process_id="p1",
        tool_name="command",
        deny_message="missing cap",
        ws_manager=None,
        session_id="sess-1",
        user_id="u1",
    )
    assert result.granted is False
    assert "esc-no-fe" in result.note
    assert "/approvals" in result.note
    assert k.approved == []
    assert k.denied == []


@pytest.mark.asyncio
async def test_capability_confirm_approve_retries_gate_path():
    from backend.agent.kernel_escalation_ui import offer_kernel_capability_confirm
    from backend.services.confirm_manager import ConfirmOutcome

    class K:
        def __init__(self):
            self.approved = []
            self.denied = []

        async def request_escalation(self, pid, caps, *, reason=""):
            return SimpleNamespace(id="esc-ok")

        async def approve_escalation(self, rid, *, by="user"):
            self.approved.append((rid, by))

        async def deny_escalation(self, rid, *, by="user"):
            self.denied.append(rid)

    k = K()
    with patch(
        "backend.services.confirm_manager.request_confirmation",
        new=AsyncMock(return_value=ConfirmOutcome(True, "approved", "once")),
    ):
        result = await offer_kernel_capability_confirm(
            kernel=k,
            process_id="p1",
            tool_name="command",
            deny_message="missing cap",
            ws_manager=object(),
            session_id="sess-1",
            user_id="u1",
        )
    assert result.granted is True
    assert k.approved == [("esc-ok", "user:chat")]
    assert k.denied == []


@pytest.mark.asyncio
async def test_capability_confirm_user_deny():
    from backend.agent.kernel_escalation_ui import offer_kernel_capability_confirm
    from backend.services.confirm_manager import ConfirmOutcome

    class K:
        def __init__(self):
            self.approved = []
            self.denied = []

        async def request_escalation(self, pid, caps, *, reason=""):
            return SimpleNamespace(id="esc-no")

        async def approve_escalation(self, rid, *, by="user"):
            self.approved.append(rid)

        async def deny_escalation(self, rid, *, by="user"):
            self.denied.append(rid)

    k = K()
    with patch(
        "backend.services.confirm_manager.request_confirmation",
        new=AsyncMock(return_value=ConfirmOutcome(False, "denied", "deny")),
    ):
        result = await offer_kernel_capability_confirm(
            kernel=k,
            process_id="p1",
            tool_name="command",
            deny_message="missing cap",
            ws_manager=object(),
            session_id="sess-1",
            user_id="u1",
        )
    assert result.granted is False
    assert "拒绝" in result.note
    assert k.denied == ["esc-no"]
    assert k.approved == []


@pytest.mark.asyncio
async def test_capability_confirm_already_present():
    from backend.agent.kernel_escalation_ui import offer_kernel_capability_confirm

    class K:
        async def request_escalation(self, pid, caps, *, reason=""):
            raise ValueError("申请的能力均已在进程能力集内")

    result = await offer_kernel_capability_confirm(
        kernel=K(),
        process_id="p1",
        tool_name="command",
        deny_message="x",
        ws_manager=object(),
        session_id="sess-1",
    )
    assert result.granted is True


def test_loop_tools_uses_in_chat_confirm():
    src = (ROOT / "backend" / "agent" / "loop_tools.py").read_text(encoding="utf-8")
    assert "offer_kernel_capability_confirm" in src
    assert "try_workforce_missing_cap_auto_grant" in src
    assert "用户在权限控制台批准后即可重试" not in src
    assert "请主人让 CEO" not in src


def test_tevarn_live_product_ids_not_takton():
    """Live UI/auth/events use tevarn-*; takton-* only as read/clear shims."""
    auth = (ROOT / "frontend" / "stores" / "authStore.ts").read_text(encoding="utf-8")
    assert "name: 'tevarn-auth'" in auth
    assert "adoptLegacyPersist('tevarn-auth')" in auth

    settings = (
        ROOT / "frontend" / "components" / "settings" / "ModelSettingsPanel.tsx"
    ).read_text(encoding="utf-8")
    assert "tevarn:settings-changed" in settings
    assert "takton:settings-changed" not in settings

    api = (ROOT / "frontend" / "lib" / "api.ts").read_text(encoding="utf-8")
    assert "AUTH_LS_KEYS = ['tevarn-auth', 'takton-auth']" in api
    assert "export type TaktonPackageItem" not in api

    page = (ROOT / "frontend" / "app" / "chat" / "page.tsx").read_text(encoding="utf-8")
    assert "DangerConfirmDialog" not in page
    assert "onUserInputIgnored" in page
    assert "handleUserInputIgnored" in page

    ws = (ROOT / "frontend" / "hooks" / "useWebSocket.ts").read_text(encoding="utf-8")
    assert "user_input_ignored" in ws
    assert "4004" in ws
    assert "sessionGoneRef" in ws
    assert "isSessionDeleted" in ws

    cron = (
        ROOT / "frontend" / "components" / "cron-webhook" / "CronWebhookPanel.tsx"
    ).read_text(encoding="utf-8")
    assert "window.confirm" not in cron
    assert "{ConfirmDialogComponent}" in cron

    inp = (ROOT / "frontend" / "components" / "chat" / "MessageInput.tsx").read_text(
        encoding="utf-8"
    )
    assert "ok === false" in inp
    assert "else if (!isStreaming)" not in inp
    assert "IconMore" in inp
    assert "chat.toolsMenu" in inp
    assert "ComposerMenuPortal" in inp
    picker = (ROOT / "frontend" / "components" / "chat" / "ModelPicker.tsx").read_text(
        encoding="utf-8"
    )
    assert "ComposerMenuPortal" in picker
    assert "handleSend('queue')" in inp
    assert "handleSend('interrupt')" in inp
    assert "handleSend('steer')" in inp
    assert "APP_VERSION" not in inp

    page = (ROOT / "frontend" / "app" / "chat" / "page.tsx").read_text(encoding="utf-8")
    assert "ComposerContextStrip" in page
    assert "ChatInspector" in page
    assert "<WorkspaceDock />" not in page
    assert "<TerminalPanel />" not in page
    assert "<TaskPanel" not in page
    assert "<TransparencyPanel" not in page
    assert "<FilePreviewHost" not in page
    assert "<ActivityPanel" not in page
    inspect = (ROOT / "frontend" / "components" / "chat" / "ChatInspector.tsx").read_text(
        encoding="utf-8"
    )
    assert "embedded" in inspect
    ctx = (ROOT / "frontend" / "components" / "chat" / "ComposerContextStrip.tsx").read_text(
        encoding="utf-8"
    )
    assert "session-artifacts-bar" in ctx


def test_loop_cluster_coerces_tool_arg_types():
    src = (ROOT / "backend" / "agent" / "loop_cluster.py").read_text(encoding="utf-8")
    assert "_coerce_tool_args" in src
    exec_src = (ROOT / "backend" / "services" / "tools" / "executors.py").read_text(
        encoding="utf-8"
    )
    assert "create_process_exec" in exec_src
    assert "asyncio.create_subprocess_exec(py, script_path" not in exec_src
