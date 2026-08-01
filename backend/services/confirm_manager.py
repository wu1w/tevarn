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
                "当前运行环境没有确认通道（定时任务 / 渠道机器人 / 无前端连接）；"
                "已按 headless 策略处理（safe 默认：读写可放行，shell/网络拒绝）。"
                "全放行可设 TAKTON_HEADLESS_AUTO_APPROVE=1 或 agent_permission_headless=allow"
            ),
            "not_connected": (
                "会话没有活跃的前端连接，确认弹窗无法送达；"
                "已按 headless 策略处理。打开应用界面可走真确认弹窗。"
            ),
            "broadcast_failed": "确认请求推送失败（连接异常）；已按 headless 策略或拒绝处理",
        }.get(self.reason, self.reason)


def _headless_auto_approve_enabled() -> bool:
    """Product flag: allow headless auto-approve when no FE (env or settings)."""
    import os

    env = os.environ.get("TAKTON_HEADLESS_AUTO_APPROVE", "").strip().lower()
    if env in ("1", "true", "yes", "on"):
        return True
    if env in ("0", "false", "no", "off"):
        return False
    try:
        from backend.core.config import settings

        return bool(getattr(settings, "agent_permission_auto_approve_no_fe", False))
    except Exception:
        return False


def _headless_safe_allow(tool: str, command: str) -> bool:
    """safe headless: allow file edits/reads; deny shell/network by tool id.

    Tool name is matched as a whole token (or exact id), not bare substring —
    avoids false deny when e.g. a path contains 'http' or 'python'.
    """
    t = (tool or "").strip().lower()
    c = (command or "").lower()
    # Exact / token tool ids (and common aliases)
    high_tools = frozenset(
        {
            "command",
            "bash",
            "shell",
            "terminal",
            "python",
            "execute_python",
            "remote",
            "browser",
            "http",
            "desktop",
            "run_command",
            "shell_session",
            "process",
        }
    )
    # tool may be "pack.command" or "CommandTool"
    base = t.split(".")[-1].replace("tool", "").strip("_")
    if t in high_tools or base in high_tools:
        return False
    # Command text: only clear high-risk patterns (not bare 'python'/'http')
    if any(
        p in c
        for p in (
            "sudo ",
            "rm -rf",
            "rm -r ",
            "format ",
            "mkfs",
            "dd if=",
            ":(){",
        )
    ):
        return False
    return True


def _confirm_single_user_mode() -> bool:
    try:
        from backend.core.config import settings

        return bool(getattr(settings, "single_user_mode", True))
    except Exception:
        return True


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
    user_id: str | None = None,
) -> ConfirmOutcome:
    """推送确认请求并等待用户决定（含授权作用域）。

    Delivery:
    - Prefer session WS; if that tab is gone, fan-out to any live WS of the same user
      (CEO often has domain/other-session tabs open).
    - Only fall to headless when no FE can receive the popup.

    Multi-user：必须带 user_id，否则 pending.owner 为空会变成「任意人可 resolve」。
    """
    if ws_manager is None:
        return _headless_confirm_outcome(tool=tool, command=command, why="no_channel")

    owner_uid = str(user_id).strip() if user_id else ""
    if not owner_uid and not _confirm_single_user_mode():
        logger.warning(
            "confirm: multi-user requires user_id (tool=%s cmd=%s)",
            tool or "-",
            command[:80],
        )
        return ConfirmOutcome(False, "user_id_required")

    sid = session_id
    if isinstance(session_id, str):
        try:
            sid = _uuid.UUID(session_id)
        except (ValueError, AttributeError):
            sid = session_id

    session_live = False
    user_live = False
    checker = getattr(ws_manager, "is_connected", None)
    if callable(checker):
        try:
            session_live = bool(checker(sid))
        except Exception as e:
            logger.debug("confirm: is_connected probe failed: %s", e)
    user_probe = getattr(ws_manager, "user_has_live_connection", None)
    if callable(user_probe) and owner_uid:
        try:
            user_live = bool(user_probe(owner_uid))
        except Exception as e:
            logger.debug("confirm: user_has_live_connection failed: %s", e)

    if not session_live and not user_live:
        logger.warning(
            "confirm: no live FE (session=%s user=%s) — headless policy for: %s",
            session_id,
            owner_uid or "-",
            command[:120],
        )
        return _headless_confirm_outcome(
            tool=tool, command=command, why="not_connected"
        )

    confirm_id = _uuid.uuid4().hex[:12]
    event = asyncio.Event()
    # 绑定 user_id：HTTP resolve 时校验，防止任意登录用户 resolve 他人 confirm
    holder: dict = {
        "approved": False,
        "scope": "deny",
        "user_id": owner_uid,
        "session_id": str(session_id) if session_id else "",
        "payload": None,  # 填入后供 sync 重放
    }
    _pending[confirm_id] = (event, holder)

    payload = {
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
        "user_id": owner_uid,
    }
    holder["payload"] = dict(payload)
    try:
        # Always try session first (even if probe said offline — race with reconnect)
        await ws_manager.broadcast(sid, payload)
        # Fan-out to *other* tabs only — exclude this session to avoid double popup
        if owner_uid and hasattr(ws_manager, "broadcast_to_user"):
            try:
                uid: object = owner_uid
                try:
                    uid = _uuid.UUID(owner_uid)
                except (ValueError, AttributeError, TypeError):
                    pass
                exclude = sid if isinstance(sid, _uuid.UUID) else None
                if exclude is None and isinstance(session_id, str):
                    try:
                        exclude = _uuid.UUID(session_id)
                    except (ValueError, AttributeError):
                        exclude = None
                await ws_manager.broadcast_to_user(
                    uid, payload, exclude_session=exclude
                )
            except Exception as ue:
                logger.debug("confirm: user fan-out skip: %s", ue)
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
        # 通知前端关窗（否则弹窗会一直挂着，用户点允许静默失败）
        try:
            expired = {
                "type": "confirm_expired",
                "confirm_id": confirm_id,
                "session_id": str(session_id) if session_id else "",
                "reason": "timeout",
                "timeout": timeout,
            }
            await ws_manager.broadcast(sid, expired)
            if owner_uid and hasattr(ws_manager, "broadcast_to_user"):
                try:
                    uid: object = owner_uid
                    try:
                        uid = _uuid.UUID(owner_uid)
                    except (ValueError, AttributeError, TypeError):
                        pass
                    await ws_manager.broadcast_to_user(uid, expired)
                except Exception:
                    pass
        except Exception as te:
            logger.debug("confirm_expired broadcast skip: %s", te)
        return ConfirmOutcome(False, "timeout", "deny")
    finally:
        _pending.pop(confirm_id, None)


def _headless_confirm_outcome(
    *, tool: str, command: str, why: str
) -> ConfirmOutcome:
    """When no FE is connected: product headless policy instead of hard deny-only."""
    if _headless_auto_approve_enabled():
        logger.info(
            "confirm: headless auto-approve (%s) tool=%s cmd=%s",
            why,
            tool or "-",
            command[:80],
        )
        return ConfirmOutcome(True, "approved", "session")
    try:
        from backend.core.config import settings

        mode = str(getattr(settings, "agent_permission_headless", "safe") or "safe").lower()
    except Exception:
        mode = "safe"
    if mode in ("allow", "local_allow", "auto_allow"):
        logger.info("confirm: headless allow (%s) tool=%s", why, tool or "-")
        return ConfirmOutcome(True, "approved", "session")
    if mode == "safe" and _headless_safe_allow(tool, command):
        logger.info("confirm: headless safe-allow (%s) tool=%s", why, tool or "-")
        return ConfirmOutcome(True, "approved", "once")
    logger.warning(
        "confirm: headless deny (%s mode=%s) tool=%s cmd=%s",
        why,
        mode,
        tool or "-",
        command[:80],
    )
    return ConfirmOutcome(False, why)


def list_pending_for_session(session_id: str | None) -> list[dict]:
    """sync 重放：该会话未决确认（前端刷新/切页后补弹窗）。"""
    sid = str(session_id or "").strip()
    if not sid:
        return []
    out: list[dict] = []
    for cid, (_ev, holder) in list(_pending.items()):
        if str(holder.get("session_id") or "") != sid:
            continue
        pl = holder.get("payload")
        if isinstance(pl, dict) and pl.get("confirm_id"):
            out.append(dict(pl))
        else:
            out.append(
                {
                    "type": "confirm_request",
                    "confirm_id": cid,
                    "session_id": sid,
                    "title": "待确认操作",
                    "command": "",
                    "reason": "rehydrate",
                    "timeout": DEFAULT_TIMEOUT,
                }
            )
    return out


def resolve_confirmation(
    confirm_id: str,
    approved: bool,
    *,
    scope: str | None = None,
    user_id: str | None = None,
) -> bool:
    """前端回传确认结果。scope: once|session|agent|deny。

    归属规则：
    - pending.owner 有值：必须与 user_id 匹配（HTTP/WS 均应传）
    - pending.owner 为空且 multi-user：拒绝（防任意登录用户 resolve）
    - single_user_mode：允许空 owner（桌面单用户兼容）
    """
    entry = _pending.get(confirm_id)
    if entry is None:
        return False
    event, holder = entry
    owner = str(holder.get("user_id") or "").strip()
    uid = str(user_id).strip() if user_id is not None else ""
    if owner:
        if not uid or uid != owner:
            logger.warning(
                "confirm: user_id mismatch confirm=%s owner=%s got=%s",
                confirm_id[:8],
                owner[:8],
                (uid or "-")[:8],
            )
            return False
    elif not _confirm_single_user_mode():
        # multi-user + 无主 pending：fail-closed（旧 hook / 编制旁路未注入 _user_id）
        logger.warning(
            "confirm: reject unbound pending in multi-user confirm=%s",
            confirm_id[:8],
        )
        return False
    holder["approved"] = bool(approved)
    if approved:
        s = (scope or "once").strip().lower()
        # 白名单：脏 scope 不得写入
        if s not in ("once", "session", "agent"):
            s = "once"
        holder["scope"] = s
    else:
        holder["scope"] = "deny"
    event.set()
    return True
