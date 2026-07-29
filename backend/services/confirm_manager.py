"""危险操作确认管理器

agent 执行危险命令前，通过 WS 向前端弹窗请求确认；
前端用户可选择：拒绝 / 允许一次 / 本会话允许 / 本员工允许。
超时无响应默认拒绝，防止 agent 卡死。

结果语义（ConfirmOutcome）：调用方必须能区分「用户拒绝了」和「压根没问到人」。
"""
from __future__ import annotations

import asyncio
import logging
import uuid as _uuid
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

# confirm_id -> (asyncio.Event, result_holder)
_pending: dict[str, tuple[asyncio.Event, dict]] = {}

DEFAULT_TIMEOUT = 120.0

UNDELIVERABLE_REASONS = frozenset({"no_channel", "not_connected", "broadcast_failed"})

ConfirmScope = Literal["once", "session", "agent", "deny"]


@dataclass(frozen=True)
class ConfirmOutcome:
    """确认结果。`bool(outcome)` 为 True 表示用户明确批准（任意 allow 作用域）。"""

    approved: bool
    reason: str  # approved | denied | timeout | no_channel | not_connected | broadcast_failed
    scope: ConfirmScope = "once"

    def __bool__(self) -> bool:
        return self.approved

    @property
    def asked(self) -> bool:
        return self.reason not in UNDELIVERABLE_REASONS

    def describe(self) -> str:
        if self.approved:
            scope_zh = {
                "once": "允许一次",
                "session": "本会话允许",
                "agent": "本员工允许",
            }.get(self.scope, "已批准")
            return f"用户已批准（{scope_zh}）"
        return {
            "denied": "用户明确拒绝",
            "timeout": f"确认请求已推送，但 {DEFAULT_TIMEOUT:.0f}s 内无人响应，按拒绝处理",
            "no_channel": (
                "当前运行环境没有确认通道（定时任务 / 渠道机器人 / 无前端连接），"
                "无法征求用户同意，已保守拒绝"
            ),
            "not_connected": (
                "会话没有活跃的前端连接，确认弹窗无法送达，已保守拒绝。"
                "请在应用界面里打开该会话后重试"
            ),
            "broadcast_failed": "确认请求推送失败（连接异常），已保守拒绝",
        }.get(self.reason, self.reason)


async def request_confirmation(
    ws_manager,
    session_id,
    *,
    title: str,
    command: str,
    reason: str = "",
    timeout: float = DEFAULT_TIMEOUT,
    tool: str = "",
    agent_id: str | None = None,
    agent_name: str | None = None,
) -> ConfirmOutcome:
    """推送确认请求并等待用户决定（含授权作用域）。"""
    if ws_manager is None:
        logger.warning("confirm: no ws_manager, auto-deny dangerous op: %s", command[:120])
        return ConfirmOutcome(False, "no_channel")

    sid = session_id
    if isinstance(session_id, str):
        try:
            sid = _uuid.UUID(session_id)
        except (ValueError, AttributeError):
            sid = session_id

    checker = getattr(ws_manager, "is_connected", None)
    if callable(checker):
        try:
            if not checker(sid):
                logger.warning(
                    "confirm: session %s has no live WS connection, auto-deny: %s",
                    session_id,
                    command[:120],
                )
                return ConfirmOutcome(False, "not_connected")
        except Exception as e:
            logger.debug("confirm: is_connected probe failed: %s", e)

    confirm_id = _uuid.uuid4().hex[:12]
    event = asyncio.Event()
    holder: dict = {"approved": False, "scope": "deny"}
    _pending[confirm_id] = (event, holder)

    try:
        await ws_manager.broadcast(
            sid,
            {
                "type": "confirm_request",
                "session_id": str(session_id),
                "confirm_id": confirm_id,
                "title": title,
                "command": command,
                "reason": reason,
                "timeout": timeout,
                "tool": tool or "",
                "agent_id": agent_id or "",
                "agent_name": agent_name or "",
                "scopes": ["once", "session", "agent", "deny"],
            },
        )
    except Exception as e:
        logger.warning("confirm: broadcast failed: %s", e)
        _pending.pop(confirm_id, None)
        return ConfirmOutcome(False, "broadcast_failed")

    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
        approved = bool(holder.get("approved"))
        scope_raw = str(holder.get("scope") or ("once" if approved else "deny")).lower()
        if scope_raw not in ("once", "session", "agent", "deny"):
            scope_raw = "once" if approved else "deny"
        if not approved:
            return ConfirmOutcome(False, "denied", "deny")
        return ConfirmOutcome(True, "approved", scope_raw)  # type: ignore[arg-type]
    except asyncio.TimeoutError:
        logger.info("confirm: timeout (%ss), auto-deny: %s", timeout, command[:120])
        return ConfirmOutcome(False, "timeout", "deny")
    finally:
        _pending.pop(confirm_id, None)


def resolve_confirmation(
    confirm_id: str,
    approved: bool,
    *,
    scope: str | None = None,
) -> bool:
    """前端回传确认结果。scope: once|session|agent|deny。"""
    entry = _pending.get(confirm_id)
    if entry is None:
        return False
    event, holder = entry
    holder["approved"] = bool(approved)
    if approved:
        s = (scope or "once").strip().lower()
        if s not in ("once", "session", "agent"):
            s = "once"
        holder["scope"] = s
    else:
        holder["scope"] = "deny"
    event.set()
    return True
