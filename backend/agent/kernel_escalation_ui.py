"""Kernel capability deny → in-chat Approve (not only a string to the model).

Court/danger confirms already use `confirm_manager`. Capability denials used to
auto-open an escalation and tell the model to send the user to `/approvals`.
This helper pushes the same `confirm_request` card, waits, then approve/deny
the kernel escalation so the current tool call can retry.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CapabilityConfirmResult:
    granted: bool
    note: str = ""
    escalation_id: str | None = None


def _sid(session_id: Any) -> str:
    return str(session_id).strip() if session_id is not None else ""


async def offer_kernel_capability_confirm(
    *,
    kernel: Any,
    process_id: str,
    tool_name: str,
    deny_message: Any,
    ws_manager: Any = None,
    session_id: Any = None,
    user_id: Any = None,
    agent_id: str | None = None,
    agent_name: str | None = None,
    capabilities: list[str] | None = None,
    arguments: dict[str, Any] | None = None,
) -> CapabilityConfirmResult:
    """Create an escalation and ask the user in chat.

    Returns granted=True when the process may retry the tool gate (user
    approved, or the capability is already present). On deny / timeout / no
    channel, granted=False and `note` is safe to append to the model error.
    """
    caps = list(capabilities or [tool_name])
    reason = f"工具调用被能力集拦截：{tool_name}"
    deny_txt = str(deny_message or "").strip()
    req = None
    try:
        req = await kernel.request_escalation(
            process_id,
            caps,
            reason=reason,
        )
    except ValueError as ve:
        msg = str(ve)
        if "均已在进程能力集内" in msg or "already" in msg.lower():
            return CapabilityConfirmResult(True, note="capability already present")
        logger.debug("request_escalation skip: %s", ve)
        return CapabilityConfirmResult(
            False,
            note=f"（无法发起权限申请：{msg}）",
        )
    except Exception as e:
        logger.warning("request_escalation failed tool=%s: %s", tool_name, e)
        return CapabilityConfirmResult(
            False,
            note="（权限申请发起失败，请稍后重试）",
        )

    esc_id = str(getattr(req, "id", "") or "")
    sid = _sid(session_id)
    uid = str(user_id).strip() if user_id else ""

    if ws_manager is None or not sid:
        return CapabilityConfirmResult(
            False,
            note=(
                f"（已发起权限申请 {esc_id}，"
                "请在权限控制台 /approvals 批准后重试；请勿重复调用本工具）"
                if esc_id
                else "（请在 /approvals 批准后重试）"
            ),
            escalation_id=esc_id or None,
        )

    from backend.services.confirm_manager import request_confirmation

    outcome = await request_confirmation(
        ws_manager,
        sid,
        user_id=uid or None,
        title="工具需要授权",
        command=tool_name,
        reason=(
            f"{deny_txt or reason}"
            + (f"（员工：{agent_name}）" if agent_name else "")
            + (f" · 申请 {esc_id}" if esc_id else "")
        ),
        tool=tool_name,
        agent_id=agent_id or "",
        agent_name=agent_name or "",
    )

    if not outcome:
        if esc_id and hasattr(kernel, "deny_escalation") and outcome.reason == "denied":
            try:
                await kernel.deny_escalation(esc_id, by="user:chat")
            except Exception as de:
                logger.debug("deny_escalation skip: %s", de)
        extra = ""
        if outcome.reason in ("timeout", "not_connected", "no_channel", "broadcast_failed"):
            extra = (
                f"（申请 {esc_id} 仍在 /approvals 等待批准）"
                if esc_id
                else "（请到 /approvals 批准）"
            )
        elif outcome.reason == "denied":
            extra = "（用户拒绝了本次授权）"
        return CapabilityConfirmResult(False, note=extra, escalation_id=esc_id or None)

    if esc_id and hasattr(kernel, "approve_escalation"):
        try:
            await kernel.approve_escalation(esc_id, by="user:chat")
        except Exception as ae:
            logger.warning("approve_escalation failed id=%s: %s", esc_id, ae)
            return CapabilityConfirmResult(
                False,
                note=f"（批准写入失败：{ae}；请到 /approvals 重试）",
                escalation_id=esc_id,
            )

    scope = getattr(outcome, "scope", "once") or "once"
    try:
        from backend.agent.grant_store import add_session_grant, grant_agent_capability

        if scope == "session":
            add_session_grant(sid, tool_name, arguments, whole_tool=True)
        elif scope == "agent":
            aid = (agent_id or "").strip() or None
            if not aid:
                try:
                    from backend.agent.grant_store import resolve_identity_id

                    aid = await resolve_identity_id(
                        arguments, contact_name=agent_name
                    )
                except Exception:
                    aid = None
            if aid:
                await grant_agent_capability(aid, tool_name)
            add_session_grant(sid, tool_name, arguments, whole_tool=True)
    except Exception as ge:
        logger.debug("post-approve grant persist skip: %s", ge)

    return CapabilityConfirmResult(True, note="user approved", escalation_id=esc_id or None)
