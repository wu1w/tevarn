"""Tool lifecycle hooks: before_tool_call / after_tool_call.

Handlers may block calls or transform arguments/results.
Built-ins: file write checkpoint (optional via settings).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
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
# 安全关键的 before handler：它们异常时必须拦住工具（fail-closed）。
# 非关键 handler（文件快照 / 历史记录）异常只记日志继续——快照失败不该
# 阻断用户的工作，但权限判定失败绝不能默认放行。
_critical_before: set[BeforeHandler] = set()


def register_before_tool_call(handler: BeforeHandler, *, critical: bool = False) -> None:
    """注册 before hook。

    critical=True 表示这是安全边界的一部分：handler 抛异常时按「拒绝」处理，
    而不是跳过它继续执行工具。
    """
    if handler not in _before_handlers:
        _before_handlers.append(handler)
    if critical:
        _critical_before.add(handler)


def register_after_tool_call(handler: AfterHandler) -> None:
    if handler not in _after_handlers:
        _after_handlers.append(handler)


def clear_tool_hooks() -> None:
    """Test isolation."""
    _before_handlers.clear()
    _after_handlers.clear()
    _critical_before.clear()


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
            if h in _critical_before:
                # fail-closed：安全 handler 崩了就当它说「不行」。
                # 此前这里一律 warning 后继续，等于任何一个 import 错误 /
                # 意外参数都能让整套权限规则静默消失。
                logger.error(
                    "critical before_tool_call handler %s failed for tool=%s; "
                    "blocking the call (fail-closed)",
                    getattr(h, "__name__", h),
                    name,
                    exc_info=True,
                )
                return BeforeHookResult(
                    block=True,
                    reason=(
                        f"[permission error] 权限检查未能完成（{type(e).__name__}: {e}），"
                        f"已按拒绝处理。这是 Takton 内部错误，请查看后端日志。"
                    ),
                    arguments=args,
                )
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
    """Snapshot target file before destructive writes (P0-D: prefer Rust host)."""
    if name not in _WRITE_TOOLS:
        return BeforeHookResult(arguments=arguments)
    try:
        from backend.core.config import settings

        if not bool(getattr(settings, "agent_file_checkpoint", True)):
            return BeforeHookResult(arguments=arguments)
    except Exception:
        pass
    args = dict(arguments or {})
    path = str(
        args.get("path")
        or args.get("file")
        or args.get("filepath")
        or args.get("file_path")
        or ""
    ).strip()
    pid = str(args.get("_kernel_process_id") or args.get("_process_id") or "").strip()
    if path and pid:
        try:
            from backend.kernel import get_kernel

            k = get_kernel()
            if hasattr(k, "_call"):
                cp = k._call(
                    "checkpoint_begin",
                    {"process_id": pid, "path": path},
                )
                if isinstance(cp, dict) and cp.get("id"):
                    args["_checkpoint_id"] = cp["id"]
                    args["_checkpoint_path"] = cp.get("backup_path") or path
                    logger.info(
                        "rust file checkpoint id=%s path=%s",
                        cp["id"][:8],
                        path[:80],
                    )
                    return BeforeHookResult(arguments=args)
        except Exception as e:
            logger.debug("rust checkpoint_begin skip: %s", e)
    try:
        from backend.agent.file_checkpoint import snapshot_path_for_tool

        snap = snapshot_path_for_tool(name, args)
        if snap:
            logger.info("file checkpoint: %s -> %s", name, snap)
            args["_checkpoint_path"] = snap
    except Exception as e:
        logger.debug("file checkpoint skipped: %s", e)
    return BeforeHookResult(arguments=args)




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
    """Phase 3.2：权限裁决经 permission_court，再处理 ask/交互通道。

    fail-closed：critical handler。workforce 由 court.steward 层裁决，不弹主人窗。
    """
    from backend.core.config import settings
    from backend.kernel.permission_court import decide_tool

    if not bool(getattr(settings, "agent_permission_enabled", True)):
        return BeforeHookResult(arguments=arguments)

    skill_contract = arguments.get("_skill_contract")
    court = await decide_tool(name, arguments, skill_contract=skill_contract)
    # 供审计 / 前端解释
    args = dict(arguments or {})
    args["_permission_court"] = court.to_audit()
    decision = court.verdict

    # 哈希链：若当前 run 挂了 kernel process，补一条 policy.decision
    try:
        pid = str(args.get("_kernel_process_id") or args.get("_process_id") or "")
        if pid:
            from backend.kernel import get_kernel

            get_kernel()._emit_policy_decision(
                pid,
                action="tool_call",
                target=name,
                outcome=decision if decision in ("allow", "deny") else "escalate",
                reason=court.reason or court.matched_rule,
                source="permission_court",
                identity=str(args.get("_identity_id") or args.get("_identity_name") or "")
                or None,
                extra=court.to_audit(),
            )
    except Exception as e:
        logger.debug("court policy emit skip: %s", e)

    if court.extra.get("_confirm_ok"):
        args["_confirm_ok"] = True

    if decision == "allow":
        logger.info(
            "permission court allow tool=%s layer=%s rule=%s",
            name,
            court.layer,
            court.matched_rule,
        )
        return BeforeHookResult(arguments=args)

    if decision == "deny":
        logger.info(
            "permission court deny tool=%s layer=%s rule=%s",
            name,
            court.layer,
            court.matched_rule,
        )
        return BeforeHookResult(
            block=True,
            reason=(
                f"[permission deny] layer={court.layer} rule={court.matched_rule} "
                f"{court.reason}"
            ),
            arguments=args,
        )

    # ── ask 分支（court 未给 session_grant）──
    # 「本员工允许」短路
    try:
        from backend.agent.grant_store import has_identity_tool_grant

        if await has_identity_tool_grant(name, arguments=args):
            logger.info(
                "permission ask→identity_cap tool=%s identity=%s",
                name,
                str(args.get("_identity_id") or args.get("_contact_agent") or "")[:16],
            )
            args["_confirm_ok"] = True
            return BeforeHookResult(arguments=args)
    except Exception as e:
        logger.debug("identity grant short-circuit skip: %s", e)

    from backend.agent.permission_overlay import build_effective_rules
    from backend.agent.permissions_rules import PermissionGate
    from backend.agent.working_mode import (
        effective_ask_mode,
        effective_permission_profile,
    )

    profile = str((court.extra or {}).get("profile") or effective_permission_profile())
    mode = str((court.extra or {}).get("mode") or "build")
    gate = PermissionGate(
        profile=profile,
        mode=mode,
        project_root=_project_root_path(),
        rules=build_effective_rules(profile),
    )

    ask_mode = effective_ask_mode()
    if ask_mode == "auto":
        has_channel = args.get("_ws_manager") is not None
        if has_channel:
            ask_mode = "interactive"
        else:
            ask_mode = _headless_fallback_mode(name, settings)
            logger.info(
                "permission ask (no approval channel) → headless %s tool=%s",
                ask_mode,
                name,
            )

    if ask_mode in ("local_allow", "allow", "auto_allow"):
        logger.info(
            "permission ask→local_allow tool=%s layer=%s",
            name,
            court.layer,
        )
        return BeforeHookResult(arguments=args)
    if ask_mode == "interactive":
        return await _interactive_approval(name, args, gate, profile)
    return BeforeHookResult(
        block=True,
        reason=(
            f"[permission ask] 需要确认但当前无人可问: layer={court.layer} "
            f"rule={court.matched_rule}。这次调用来自无人值守路径（定时任务 / 渠道机器人 / Webhook），"
            f"已按拒绝处理。如需放行，请在权限控制台把「无人值守兜底」改为放行，"
            f"或把该工具加入白名单。"
        ),
        arguments=args,
    )



# 无人值守（cron / 渠道机器人 / webhook）下，即使规则说「问用户」也没人可问。
# 这些恰恰是**外部内容进入你本机**的入口 —— 一封邮件、一条群消息里的提示词注入，
# 走的就是这条路。旧默认是无差别放行，等于整套权限规则在这条路上完全不存在。
#
# 但一刀切拒绝会静默弄坏用户已有的定时任务（多数只是读文件、整理、写报告）。
# 折中：默认只拦「能执行任意代码 / 能把数据发出去」的那一类，读写照常。
_HEADLESS_HIGH_RISK_KEYS = frozenset({"bash", "desktop"})


def _headless_fallback_mode(tool_name: str, settings: Any) -> str:
    """无确认通道时的兜底决策。

    agent_permission_headless:
      - "allow" —— 全放行（0.3.x 的旧行为，需显式选择）
      - "safe"  —— 默认：读/写放行，shell·python·remote_exec·http·browser 拒绝
      - "deny"  —— 全拒绝
    """
    fallback = str(
        getattr(settings, "agent_permission_headless", "safe") or "safe"
    ).strip().lower()

    if fallback == "allow":
        return "local_allow"
    if fallback == "deny":
        return "deny"

    # safe：按权限 key 分流
    from backend.agent.permissions_rules import TOOL_TO_KEY

    key = TOOL_TO_KEY.get(tool_name, tool_name)
    if key in _HEADLESS_HIGH_RISK_KEYS:
        return "deny"
    return "local_allow"


def _tool_self_declares_confirmation(name: str) -> bool:
    """工具自称需要确认，且 PermissionGate 规则未覆盖它。

    内置工具都有规则覆盖（TOOL_TO_KEY），走 profile 语义；
    自定义 / MCP / DB 工具没有规则，此前 requires_confirmation=True 完全不起作用。
    """
    try:
        from backend.agent.permissions_rules import TOOL_TO_KEY

        if name in TOOL_TO_KEY:
            return False
        from backend.tools.registry import ToolRegistry

        tool = ToolRegistry.get(name)
        return bool(tool is not None and getattr(tool, "requires_confirmation", False))
    except Exception:
        return False


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
        from backend.agent.grant_store import add_session_grant, grant_agent_capability
        from backend.services.confirm_manager import request_confirmation

        agent_id = (
            str(arguments.get("_identity_id") or arguments.get("identity_id") or "").strip()
            or None
        )
        agent_name = str(arguments.get("_identity_name") or arguments.get("agent_name") or "").strip() or None
        # 联系员工会话：config.contact_agent 名字解析（尽力）
        if not agent_id:
            try:
                contact = str(arguments.get("_contact_agent") or "").strip()
                if contact:
                    agent_name = agent_name or contact
                    from backend.kernel import get_kernel

                    reg = getattr(get_kernel(), "identity_registry", None)
                    if reg is not None:
                        for ident in await reg.list(status="active"):
                            if ident.name == contact:
                                agent_id = str(ident.id)
                                agent_name = ident.name
                                break
            except Exception:
                pass

        await _publish("approval.requested")
        await _transition("waiting", note=f"approval: {name}")
        outcome = await request_confirmation(
            ws_manager,
            session_id,
            title="危险操作确认",
            command=summary,
            reason=f"权限 profile={profile}，工具 {name} 需要确认"
            + (f"（员工：{agent_name}）" if agent_name else ""),
            tool=name,
            agent_id=agent_id,
            agent_name=agent_name,
        )
        approved = bool(outcome)
        scope = getattr(outcome, "scope", "once") or "once"
        extra_note = ""
        if approved and scope == "session":
            add_session_grant(str(session_id) if session_id else None, name, arguments)
            extra_note = "session_grant"
        elif approved and scope == "agent":
            # 尽力解析员工 id（联系 TA 会话常只有 contact 名）
            if not agent_id:
                try:
                    from backend.agent.grant_store import resolve_identity_id

                    agent_id = await resolve_identity_id(
                        arguments, contact_name=agent_name
                    )
                except Exception:
                    pass
            ok, msg = await grant_agent_capability(agent_id, name)
            if not ok:
                # 无员工绑定：降级为本会话（整工具，避免只放行 command:rm）
                add_session_grant(
                    str(session_id) if session_id else None,
                    name,
                    arguments,
                    whole_tool=True,
                )
                extra_note = f"agent_grant_fallback_session: {msg}"
            else:
                extra_note = msg
                # 整工具会话缓存 + 持久编制能力：后续任意 command 不再弹
                add_session_grant(
                    str(session_id) if session_id else None,
                    name,
                    arguments,
                    whole_tool=True,
                )

        await _publish(
            "approval.resolved",
            {"approved": approved, "outcome": outcome.reason, "scope": scope, "note": extra_note},
        )
        await _transition("executing", note=f"approval {outcome.reason}/{scope}: {name}")
        if approved:
            logger.info(
                "permission ask→user approved tool=%s scope=%s %s",
                name,
                scope,
                summary,
            )
            # 标记本轮已确认，避免 execute_command/python 内二次弹窗
            out_args = dict(arguments)
            out_args["_confirm_ok"] = True
            out_args["_confirm_scope"] = scope
            return BeforeHookResult(arguments=out_args)
        return BeforeHookResult(
            block=True,
            reason=f"[permission blocked] {summary} — {outcome.describe()}",
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
            # P1-7: 关联工单 rewind 路径
            job_id = str(
                arguments.get("_inbox_item_id") or arguments.get("_job_id") or ""
            )
            if job_id and path:
                try:
                    from backend.agent.job_rewind import note_job_path

                    note_job_path(job_id, str(path))
                except Exception:
                    pass
    except Exception as e:
        logger.debug("file_history before skipped: %s", e)
    return BeforeHookResult(arguments=arguments)


def ensure_builtin_hooks_registered() -> None:
    if builtin_write_checkpoint_before not in _before_handlers:
        register_before_tool_call(builtin_write_checkpoint_before)
    if builtin_permission_before not in _before_handlers:
        # 唯一的安全关键 hook：它是权限体系在工具层的落点
        register_before_tool_call(builtin_permission_before, critical=True)
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
