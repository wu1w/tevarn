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


def ensure_builtin_hooks_registered() -> None:
    if builtin_write_checkpoint_before not in _before_handlers:
        register_before_tool_call(builtin_write_checkpoint_before)


__all__ = [
    "BeforeHookResult",
    "register_before_tool_call",
    "register_after_tool_call",
    "clear_tool_hooks",
    "run_before_tool_call",
    "run_after_tool_call",
    "ensure_builtin_hooks_registered",
    "builtin_write_checkpoint_before",
]
