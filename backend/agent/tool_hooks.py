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
    """Last-match permission gate (code permissions_rules).

    fail-closed：这个函数是权限体系在工具层唯一的落点，任何未预期的异常都会
    冒泡给 run_before_tool_call（本 handler 以 critical=True 注册），由那里
    按「拒绝」处理。**不要**在这里加 catch-all 兜底 return —— 那正是此前
    整套权限规则可以被一个 import 错误静默抹掉的原因。

    唯一的例外是用户显式关掉了权限系统（agent_permission_enabled=False），
    那是明确的意图表达，放行。

    员工工单（workforce）：**绝不**弹主人确认窗；由 CEO 编制能力策略裁决
    （见 steward_permission）。主人只批策略/节点，不批每一次工具。
    """
    from backend.core.config import settings

    if not bool(getattr(settings, "agent_permission_enabled", True)):
        return BeforeHookResult(arguments=arguments)

    # ── 编制员工路径：CEO 策略，不弹主人 ──
    try:
        from backend.agent.steward_permission import (
            is_human_strategy_surface,
            is_workforce_context,
            load_identity_capabilities,
            steward_decide_tool,
        )

        if is_workforce_context(arguments) and not is_human_strategy_surface(name):
            # 始终从 DB 刷新编制能力：CEO 中途 grant_caps 后下一刀工具立即生效
            iid = str(arguments.get("_identity_id") or "").strip() or None
            caps = await load_identity_capabilities(iid)
            if not isinstance(caps, list):
                raw = arguments.get("_identity_capabilities")
                caps = list(raw) if isinstance(raw, list) else None
            args = dict(arguments)
            if caps is not None:
                args["_identity_capabilities"] = list(caps)
            decision, why = await steward_decide_tool(
                name, args, identity_capabilities=caps
            )
            if decision == "allow":
                logger.info("permission workforce→steward allow tool=%s %s", name, why)
                return BeforeHookResult(arguments=args)
            logger.info("permission workforce→steward deny tool=%s %s", name, why)
            return BeforeHookResult(
                block=True,
                reason=f"[steward deny] {why}",
                arguments=args,
            )
    except Exception as e:
        # 编制路径自身故障：fail-closed，禁止回落成人弹窗刷屏
        if str(arguments.get("_agent_key") or "").startswith("wf:") or arguments.get(
            "_workforce"
        ):
            logger.warning("steward permission failed, deny workforce tool: %s", e)
            return BeforeHookResult(
                block=True,
                reason=f"[steward error] 编制权限裁决失败，已拒绝: {e}",
                arguments=arguments,
            )

    from backend.agent.permission_overlay import build_effective_rules
    from backend.agent.permissions_rules import PermissionGate
    from backend.agent.working_mode import (
        effective_ask_mode,
        effective_permission_profile,
    )

    # profile / ask_mode 由「工作方式」派生（高级用户可显式覆盖），见 working_mode.py
    profile = effective_permission_profile()
    # sandbox profile may force readonly (read_only profile)
    try:
        from backend.computer.profiles import resolve_profile

        sprof = resolve_profile()
        if sprof.force_working_mode == "readonly":
            profile = "plan"
    except Exception:
        pass
    # session mode overlay: if profile is plan OR chat mode plan OR unapproved plan gate
    mode = "build"
    chat_mode = str(arguments.get("_chat_mode") or getattr(settings, "_active_chat_mode", "") or "")
    session_id = str(arguments.get("_session_id") or "") or None
    job_id = str(arguments.get("_inbox_item_id") or arguments.get("_job_id") or "") or None
    try:
        from backend.agent.plan_session import requires_plan_approval

        if requires_plan_approval(
            session_id=session_id, job_id=job_id, chat_mode=chat_mode
        ):
            mode = "plan"
            profile = "plan"
    except Exception:
        pass
    if profile.lower() == "plan" or chat_mode.lower() in ("plan", "ask", "explore"):
        mode = "plan"
        if profile.lower() != "plan":
            profile = "plan"
    gate = PermissionGate(
        profile=profile,
        mode=mode,
        project_root=_project_root_path(),
        rules=build_effective_rules(profile),
    )
    decision = gate.check(name, arguments)

    # 工具自声明 requires_confirmation：此前该标志全项目无人读取（死标志）。
    # 现在把它接进唯一决策器 —— 仅用于**收紧**：规则说 allow 但工具自称高危时升级为 ask。
    # 只对规则未覆盖的工具生效（自定义 / MCP 工具），避免和 profile 语义打架：
    # 比如 acceptEdits 明确表达「工作区编辑不要问我」，就不该被 file_write 的声明推翻。
    if decision == "allow" and _tool_self_declares_confirmation(name):
        decision = "ask"

    # 本会话授权短路（危险弹窗「本会话允许」写入 grant_store）
    try:
        from backend.agent.grant_store import has_session_grant

        sid = str(arguments.get("_session_id") or "")
        if decision == "ask" and has_session_grant(sid, name, arguments):
            logger.info("permission ask→session_grant tool=%s", name)
            decision = "allow"
            # 同步标记，下游 executor 危险策略不再二次弹窗
            args = dict(arguments)
            args["_confirm_ok"] = True
            return BeforeHookResult(arguments=args)
    except Exception:
        pass

    # 「本员工允许」短路：Identity.capabilities 已含 command/file_rw 等 → 不再弹窗
    # （此前只写了编制能力，联系 TA 会话仍按 profile 反复 ask —— 用户感知 bug）
    if decision == "ask":
        try:
            from backend.agent.grant_store import has_identity_tool_grant

            if await has_identity_tool_grant(name, arguments=arguments):
                logger.info(
                    "permission ask→identity_cap tool=%s identity=%s",
                    name,
                    str(arguments.get("_identity_id") or arguments.get("_contact_agent") or "")[:16],
                )
                args = dict(arguments)
                args["_confirm_ok"] = True
                return BeforeHookResult(arguments=args)
        except Exception as e:
            logger.debug("identity grant short-circuit skip: %s", e)

    if decision == "allow":
        return BeforeHookResult(arguments=arguments)
    if decision == "deny":
        return BeforeHookResult(
            block=True,
            reason=f"[permission deny] {gate.summarize(name, arguments)}",
            arguments=arguments,
        )

    # ── ask 分支 ──
    ask_mode = effective_ask_mode()
    if ask_mode == "auto":
        # 有确认通道就真弹窗；没有（cron / 渠道机器人 / webhook）走兜底策略。
        has_channel = arguments.get("_ws_manager") is not None
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
        logger.info("permission ask→local_allow tool=%s %s", name, gate.summarize(name, arguments))
        return BeforeHookResult(arguments=arguments)
    if ask_mode == "interactive":
        return await _interactive_approval(name, arguments, gate, profile)
    return BeforeHookResult(
        block=True,
        reason=(
            f"[permission ask] 需要确认但当前无人可问: {gate.summarize(name, arguments)}。"
            f"这次调用来自无人值守路径（定时任务 / 渠道机器人 / Webhook），"
            f"已按拒绝处理。如需放行，请在权限控制台把「无人值守兜底」改为放行，"
            f"或把该工具加入白名单。"
        ),
        arguments=arguments,
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
