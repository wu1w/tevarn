"""Tool lifecycle hooks: before_tool_call / after_tool_call.

Handlers may block calls or transform arguments/results.
Built-ins: file write checkpoint (optional via settings).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

BeforeHandler = Callable[[str, dict[str, Any]], "BeforeHookResult | Awaitable[BeforeHookResult]"]
AfterHandler = Callable[[str, dict[str, Any], str], "str | Awaitable[str]"]


@dataclass
class BeforeHookResult:
    block: bool = False
    reason: str = ""
    arguments: dict[str, Any] | None = None  # rewritten args


_before_handlers: list[BeforeHandler] = []
_after_handlers: list[AfterHandler] = []


def register_before_tool_call(handler: BeforeHandler) -> None:
    if handler not in _before_handlers:
        _before_handlers.append(handler)


def register_after_tool_call(handler: AfterHandler) -> None:
    if handler not in _after_handlers:
        _after_handlers.append(handler)


def clear_tool_hooks() -> None:
    """Test isolation."""
    _before_handlers.clear()
    _after_handlers.clear()


async def run_before_tool_call(name: str, arguments: dict[str, Any]) -> BeforeHookResult:
    args = dict(arguments or {})
    for h in list(_before_handlers):
        try:
            res = h(name, args)
            if hasattr(res, "__await__"):
                res = await res  # type: ignore[misc]
            if not isinstance(res, BeforeHookResult):
                continue
            if res.arguments is not None:
                args = dict(res.arguments)
            if res.block:
                return BeforeHookResult(block=True, reason=res.reason or "blocked by hook", arguments=args)
        except Exception as e:
            logger.warning("before_tool_call handler error: %s", e)
    return BeforeHookResult(block=False, arguments=args)


async def run_after_tool_call(name: str, arguments: dict[str, Any], result: str) -> str:
    out = result
    for h in list(_after_handlers):
        try:
            res = h(name, arguments, out)
            if hasattr(res, "__await__"):
                res = await res  # type: ignore[misc]
            if isinstance(res, str):
                out = res
        except Exception as e:
            logger.warning("after_tool_call handler error: %s", e)
    return out


# ── built-in: write checkpoint ─────────────────────────────────

_WRITE_TOOLS = frozenset(
    {
        "file_write",
        "edit",
        "apply_patch",
        "desktop_write_file",
    }
)


async def builtin_write_checkpoint_before(name: str, arguments: dict[str, Any]) -> BeforeHookResult:
    """Snapshot target file before destructive writes."""
    if name not in _WRITE_TOOLS:
        return BeforeHookResult(arguments=arguments)
    try:
        from backend.core.config import settings

        if not bool(getattr(settings, "agent_file_checkpoint", True)):
            return BeforeHookResult(arguments=arguments)
    except Exception:
        pass
    try:
        from backend.agent.file_checkpoint import snapshot_path_for_tool

        snap = snapshot_path_for_tool(name, arguments)
        if snap:
            logger.info("file checkpoint: %s -> %s", name, snap)
            # non-blocking note in args meta (not sent to tool if stripped)
            arguments = dict(arguments)
            arguments["_checkpoint_path"] = snap
    except Exception as e:
        logger.debug("file checkpoint skipped: %s", e)
    return BeforeHookResult(arguments=arguments)




# ── Batch2: permission rules + file history ────────────────────

_EDIT_TOOLS = frozenset(
    {
        "file_write",
        "edit",
        "apply_patch",
        "desktop_write_file",
        "doc_write",
    }
)


def _project_root_path():
    try:
        from backend.tools.permissions import resolve_agent_workspace_root

        return __import__("pathlib").Path(resolve_agent_workspace_root())
    except Exception:
        from pathlib import Path

        return Path.cwd()


async def builtin_permission_before(name: str, arguments: dict[str, Any]) -> BeforeHookResult:
    """Last-match permission gate (code permissions_rules)."""
    try:
        from backend.core.config import settings

        if not bool(getattr(settings, "agent_permission_enabled", True)):
            return BeforeHookResult(arguments=arguments)
    except Exception:
        return BeforeHookResult(arguments=arguments)

    try:
        from backend.agent.permissions_rules import PermissionGate, rules_for_profile

        profile = str(getattr(settings, "agent_permission_profile", "cautious") or "cautious")
        # session mode overlay: if profile is plan OR chat mode plan
        mode = "build"
        chat_mode = str(arguments.get("_chat_mode") or getattr(settings, "_active_chat_mode", "") or "")
        if profile.lower() == "plan" or chat_mode.lower() in ("plan", "ask", "explore"):
            mode = "plan"
            if profile.lower() != "plan":
                # overlay plan rules when chat mode is plan
                profile = "plan"
        gate = PermissionGate(
            profile=profile,
            mode=mode,
            project_root=_project_root_path(),
            rules=rules_for_profile(profile),
        )
        decision = gate.check(name, arguments)
        if decision == "allow":
            return BeforeHookResult(arguments=arguments)
        if decision == "deny":
            return BeforeHookResult(
                block=True,
                reason=f"[permission deny] {gate.summarize(name, arguments)}",
                arguments=arguments,
            )
        # ask
        ask_mode = str(getattr(settings, "agent_permission_ask_mode", "local_allow") or "local_allow")
        if ask_mode in ("local_allow", "allow", "auto_allow"):
            logger.info("permission ask→local_allow tool=%s %s", name, gate.summarize(name, arguments))
            return BeforeHookResult(arguments=arguments)
        if ask_mode == "interactive":
            return await _interactive_approval(name, arguments, gate, profile)
        return BeforeHookResult(
            block=True,
            reason=(
                f"[permission ask] 需要确认: {gate.summarize(name, arguments)}. "
                f"单用户可设 agent_permission_ask_mode=local_allow"
            ),
            arguments=arguments,
        )
    except Exception as e:
        logger.debug("permission hook skipped: %s", e)
        return BeforeHookResult(arguments=arguments)


async def _interactive_approval(
    name: str, arguments: dict[str, Any], gate: Any, profile: str
) -> BeforeHookResult:
    """agent_permission_ask_mode=interactive：WS 弹窗真确认（Phase 0.5.2 W2-3）

    - 经 confirm_manager 推送 confirm_request 并挂起等待用户决定（超时默认拒绝）
    - 等待期间 Run 状态机转 WAITING，结束后回 EXECUTING
    - approval.requested / approval.resolved 事件进 EventBus（活动流可见）
    - 通道异常时保守拒绝（宁拦不放）
    """
    summary = gate.summarize(name, arguments)
    session_id = arguments.get("_session_id")
    ws_manager = arguments.get("_ws_manager")
    recorder = arguments.get("_run_recorder")
    bus_payload = {
        "session_id": str(session_id) if session_id else None,
        "run_id": str(recorder.run_id) if recorder is not None and recorder.run_id else None,
        "tool": name,
        "summary": summary,
        "profile": profile,
    }

    async def _publish(topic: str, extra: dict[str, Any] | None = None) -> None:
        try:
            from backend.core.event_bus import event_bus

            await event_bus.publish(topic, {**bus_payload, **(extra or {})})
        except Exception:
            pass

    async def _transition(dst: str, note: str) -> None:
        if recorder is None:
            return
        try:
            await recorder.transition(dst, note=note)
        except Exception:
            pass

    try:
        from backend.services.confirm_manager import request_confirmation

        await _publish("approval.requested")
        await _transition("waiting", note=f"approval: {name}")
        approved = await request_confirmation(
            ws_manager,
            session_id,
            title="工具调用确认",
            command=summary,
            reason=f"权限 profile={profile}，工具 {name} 需要确认",
        )
        await _publish("approval.resolved", {"approved": approved})
        await _transition(
            "executing", note=f"approval {'approved' if approved else 'denied'}: {name}"
        )
        if approved:
            logger.info("permission ask→user approved tool=%s %s", name, summary)
            return BeforeHookResult(arguments=arguments)
        return BeforeHookResult(
            block=True,
            reason=f"[permission denied by user] {summary}",
            arguments=arguments,
        )
    except Exception as e:
        logger.warning("interactive approval failed, deny: %s", e)
        await _publish("approval.resolved", {"approved": False, "error": str(e)})
        await _transition("executing", note=f"approval channel error: {name}")
        return BeforeHookResult(
            block=True,
            reason=f"[permission ask] 确认通道异常，已保守拒绝: {summary}",
            arguments=arguments,
        )


async def builtin_file_history_before(name: str, arguments: dict[str, Any]) -> BeforeHookResult:
    """Snapshot into FileHistory before edit tools."""
    if name not in _EDIT_TOOLS:
        return BeforeHookResult(arguments=arguments)
    try:
        from backend.core.config import settings

        if not bool(getattr(settings, "agent_file_history", True)):
            return BeforeHookResult(arguments=arguments)
    except Exception:
        return BeforeHookResult(arguments=arguments)
    try:
        from backend.agent.file_history import get_file_history

        root = _project_root_path()
        sid = str(arguments.get("_session_id") or "default")
        path = (
            arguments.get("filepath")
            or arguments.get("path")
            or arguments.get("file")
            or ""
        )
        paths = [str(path)] if path else []
        hist = get_file_history(root, sid)
        if paths:
            pt = hist.create_point(paths=paths, label=f"before:{name}", kind="pre_write")
            arguments = dict(arguments)
            arguments["_history_point"] = pt.id
            logger.info("file_history point %s for %s", pt.id, paths)
    except Exception as e:
        logger.debug("file_history before skipped: %s", e)
    return BeforeHookResult(arguments=arguments)


def ensure_builtin_hooks_registered() -> None:
    if builtin_write_checkpoint_before not in _before_handlers:
        register_before_tool_call(builtin_write_checkpoint_before)
    if builtin_permission_before not in _before_handlers:
        register_before_tool_call(builtin_permission_before)
    if builtin_file_history_before not in _before_handlers:
        register_before_tool_call(builtin_file_history_before)

__all__ = [
    "BeforeHookResult",
    "register_before_tool_call",
    "register_after_tool_call",
    "clear_tool_hooks",
    "run_before_tool_call",
    "run_after_tool_call",
    "ensure_builtin_hooks_registered",
    "builtin_write_checkpoint_before",
    "builtin_permission_before",
    "builtin_file_history_before",
]
