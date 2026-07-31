"""工具路径中央门控（Hardening debt #1）。

所有工具执行路径必须经本模块：

- ``loop_tools._execute_registered_tool``（主路径，含编制扩权）
- ``ToolRegistry.execute``（含被直接调用的 DB/MCP/脚本）
- ``tool_round`` 遗留 SkillRegistry / DB skill 路径

设计：

1. **幂等**：``_tool_gate_passed`` 标记避免 double mediate / double charge。
2. **fail-closed（Agent 上下文）**：kernel 开启且调用带 ``_session_id`` /
   ``_workforce`` / ``_require_kernel_process``，但没有 process_id → 拒绝。
3. **兼容（单测/脚本）**：无 Agent 上下文且无 process_id → 放行（不 mediate）。
4. **资源账户**：mediate 成功后扣 ``tool_calls``；命令类再扣 ``child_proc``。
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# 与 loop_tools / ComputerManager 对齐的「会起子进程」工具名
CHILD_PROC_TOOLS = frozenset({
    "command",
    "bash",
    "shell",
    "python",
    "process",
    "terminal",
    "computer",
})

# Agent 上下文线索：出现任一即视为「正式 run」，缺 process 时 fail-closed。
# 注意：_session_id 单独不算（确认弹窗/探针需要 session 穿透但未必有 kernel process）。
_AGENT_CONTEXT_KEYS = (
    "_workforce",
    "_require_kernel_process",
    "_inbox_item_id",
    "_run_recorder",
    "_kernel_process_id",
    "_process_id",
)

# 注入到工具 args 的服务端对象，绝不可进入 Rust RPC / 审计 JSON。
_INTERNAL_ARG_DROP = frozenset({
    "_ws_manager",
    "ws_manager",
    "connection_manager",
    "_run_recorder",
    "_tool_gate_passed",
    "_tool_gate_internal",
})


def sanitize_args_for_kernel(arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Strip non-JSON / live objects before kernel mediate / charge / RPC.

    tool_round injects ``_ws_manager=ConnectionManager`` for confirmation UI;
    that must never be serialized into host ``mediate`` params (TypeError →
    total tool paralysis).
    """
    if not isinstance(arguments, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in arguments.items():
        ks = str(k)
        if ks in _INTERNAL_ARG_DROP:
            continue
        # Live service objects (WebSocket manager, DB sessions, etc.)
        tname = type(v).__name__
        if tname in (
            "ConnectionManager",
            "AsyncSession",
            "Session",
            "ClientSession",
            "WebSocket",
        ):
            continue
        if callable(v) and not isinstance(v, (str, bytes, bytearray)):
            continue
        try:
            out[ks] = json.loads(json.dumps(v, ensure_ascii=False, default=str))
        except Exception:
            try:
                out[ks] = str(v)[:2000]
            except Exception:
                continue
    return out


def extract_process_id(arguments: dict[str, Any] | None) -> str | None:
    args = arguments or {}
    pid = str(
        args.get("_kernel_process_id")
        or args.get("_process_id")
        or ""
    ).strip()
    if pid:
        return pid
    rec = args.get("_run_recorder")
    if rec is not None:
        pid = str(getattr(rec, "kernel_process_id", "") or "").strip()
        if pid:
            return pid
    return None


def has_agent_context(arguments: dict[str, Any] | None) -> bool:
    args = arguments or {}
    if args.get("_require_kernel_process"):
        return True
    if args.get("_workforce") is True:
        return True
    if str(args.get("_agent_key") or "").startswith("wf:"):
        return True
    for k in _AGENT_CONTEXT_KEYS:
        if k == "_workforce":
            continue
        v = args.get(k)
        if v is not None and v != "" and v is not False:
            return True
    return False


def _kernel_enabled() -> bool:
    try:
        from backend.core.config import settings

        enabled = bool(getattr(settings, "agent_kernel_enabled", True))
        if enabled:
            return True
        # H2-A3: disabling kernel only legal under DEV_UNSAFE / test
        from backend.kernel.production_guard import allow_kernel_disabled

        if allow_kernel_disabled():
            logger.warning(
                "agent_kernel_enabled=False accepted (DEV_UNSAFE/test only)"
            )
            return False
        logger.error(
            "H2: agent_kernel_enabled=False ignored in production — "
            "set TAKTON_DEV_UNSAFE=1 to allow ungoverned mode"
        )
        return True
    except Exception:
        return True


def _charge(kernel: Any, process_id: str, kind: str, amount: int = 1) -> None:
    if hasattr(kernel, "resource_charge"):
        kernel.resource_charge(process_id, kind, amount)
        return
    if hasattr(kernel, "_call"):
        kernel._call(
            "resource_charge",
            {"process_id": process_id, "kind": kind, "amount": amount},
        )
        return
    raise RuntimeError("kernel has no resource_charge")


async def mediate_tool_call(
    name: str,
    process_id: str,
    arguments: dict[str, Any] | None = None,
) -> None:
    """对已有 process 做 mediate。拒绝时抛 KernelPermissionError。"""
    from backend.kernel import get_kernel

    k = get_kernel()
    await k.mediate(
        process_id,
        "tool_call",
        name,
        args=sanitize_args_for_kernel(arguments),
    )


def charge_for_tool(name: str, process_id: str, arguments: dict[str, Any] | None = None) -> None:
    """扣 tool_calls（+ 命令类 child_proc + 大参数 io 预估）。配额不足抛异常。"""
    from backend.kernel import get_kernel

    k = get_kernel()
    # K-05：内存已超限则拒绝任何新工具（含非 command）
    try:
        usage = k.resource_usage(process_id) if hasattr(k, "resource_usage") else None
        if isinstance(usage, dict):
            mem = usage.get("memory_bytes") or {}
            mlim, mused = mem.get("limit"), mem.get("used")
            if mlim is not None and mused is not None and int(mused) > int(mlim):
                raise RuntimeError(
                    f"memory_bytes exceeded: used={mused} limit={mlim}"
                )
    except RuntimeError:
        raise
    except Exception:
        pass
    _charge(k, process_id, "tool_calls", 1)
    if name in CHILD_PROC_TOOLS:
        _charge(k, process_id, "child_proc", 1)
    # T3：按参数体量预扣 io_write_bytes（逻辑账户，防止 runaway 写）
    try:
        raw = json.dumps(
            sanitize_args_for_kernel(arguments),
            ensure_ascii=False,
            default=str,
        )
        nbytes = len(raw.encode("utf-8", errors="replace"))
        if nbytes >= 4096:
            _charge(k, process_id, "io_write_bytes", nbytes)
    except Exception:
        pass


async def enforce_tool_gate(
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    process_id: str | None = None,
    require_process: bool | None = None,
    charge: bool = True,
    mark_passed: bool = True,
) -> tuple[dict[str, Any], str | None]:
    """统一门控。

    Returns:
        (args, error_or_none)
        - error 非空：调用方应直接把 error 作为工具结果返回，不得执行工具。
        - 成功时 args 可能被注入 ``_kernel_process_id`` / ``_tool_gate_passed``。
    """
    args = dict(arguments or {})
    # 安全：_tool_gate_passed 仅信任本进程内部标记，拒绝模型/客户端注入
    # （LLM 若在 arguments 里塞 True 会绕过 mediate）
    client_claimed_passed = bool(args.pop("_tool_gate_passed", None))
    internal_ok = bool(args.pop("_tool_gate_internal", None))
    if internal_ok and client_claimed_passed:
        # 仅内部二次进入（loop→registry）可跳过；需同时带 internal token
        args["_tool_gate_passed"] = True
        args["_tool_gate_internal"] = True
        return args, None
    # 丢弃伪造的 passed，强制重审
    args.pop("_tool_gate_passed", None)
    args.pop("_tool_gate_internal", None)

    if not _kernel_enabled():
        if mark_passed:
            args["_tool_gate_passed"] = True
            args["_tool_gate_internal"] = True
        return args, None

    # process_id 形参优先（loop 传入），覆盖 args 内任何残留
    if process_id and str(process_id).strip():
        pid = str(process_id).strip()
        args["_kernel_process_id"] = pid
    else:
        pid = (extract_process_id(args) or "").strip() or None
        if pid:
            args["_kernel_process_id"] = pid

    must = require_process if require_process is not None else has_agent_context(args)

    if not pid:
        if must:
            return args, (
                f"Error: Kernel 门控拒绝——工具 «{name}» 缺少 process 上下文"
                "（_kernel_process_id）。Agent 路径必须经 create_process 后 mediate，"
                "禁止绕过。请重试会话或检查 agent_kernel_enabled 接线。"
            )
        # 单测 / 裸脚本：无进程不 mediate
        if mark_passed:
            args["_tool_gate_passed"] = True
            args["_tool_gate_internal"] = True
        return args, None

    from backend.kernel import KernelPermissionError, get_kernel

    # Mediate only JSON-safe user args (never live ConnectionManager / recorder).
    mediate_args = sanitize_args_for_kernel(args)
    try:
        k = get_kernel()
        await k.mediate(pid, "tool_call", name, args=mediate_args)
    except KernelPermissionError as e:
        logger.warning("tool_gate mediate deny tool=%s proc=%s: %s", name, pid[:12], e)
        return args, f"Error: Kernel 权限拒绝——{e}"
    except PermissionError as e:
        # 兼容历史/第三方 PermissionError；能力拒绝不是「门控故障」
        logger.warning("tool_gate mediate deny(PE) tool=%s proc=%s: %s", name, pid[:12], e)
        return args, f"Error: Kernel 权限拒绝——{e}"
    except ValueError as e:
        # host not_found（未知进程）— 调用方可 rehydrate
        msg = str(e)
        logger.warning("tool_gate mediate value error tool=%s proc=%s: %s", name, pid[:12], e)
        if "未知" in msg or "not found" in msg.lower() or "NotFound" in msg:
            return args, f"Error: Kernel 权限拒绝——{msg}"
        return args, f"Error: Kernel 门控故障（ValueError: {e}），已拒绝 «{name}»。"
    except Exception as e:
        # fail-closed：kernel 故障不得静默放行
        # Connection/timeout are recoverable via rehydrate — but do NOT mislabel
        # them as「未知进程」(that confused CEO into blaming employee channels).
        msg = str(e)
        low = msg.lower()
        if (
            "closed connection" in low
            or "10053" in msg
            or "10054" in msg
            or "not connected" in low
            or "read timeout" in low
            or "write timeout" in low
            or isinstance(e, (ConnectionError, TimeoutError, BrokenPipeError, OSError))
        ):
            logger.warning(
                "tool_gate mediate host disconnect tool=%s proc=%s: %s",
                name,
                pid[:12],
                e,
            )
            return args, (
                f"Error: Kernel host 重连中——process={pid} "
                f"({type(e).__name__}: {msg[:160]})；将自动 rehydrate 后重试"
            )
        logger.error(
            "tool_gate mediate failed tool=%s proc=%s: %s",
            name,
            pid[:12],
            e,
            exc_info=True,
        )
        return args, (
            f"Error: Kernel 门控故障（{type(e).__name__}: {e}），已拒绝 «{name}»。"
        )

    if charge:
        try:
            charge_for_tool(name, pid, args)
        except Exception as rce:
            logger.warning("tool_gate resource_charge deny tool=%s: %s", name, rce)
            return args, (
                f"Error: 资源配额不足——{rce}。"
                "请降低调用频率或提高进程资源上限。"
            )

    if mark_passed:
        args["_tool_gate_passed"] = True
        # 内部令牌：仅服务端二次调用可跳过；模型不可伪造（每次入口 pop）
        args["_tool_gate_internal"] = True
    return args, None


def workforce_sandbox_fail_message(
    *,
    profile_id: str | None = None,
    detail: str = "",
    capability_note: str = "",
) -> str:
    """编制员工无可用沙箱时的统一 fail-closed 文案。"""
    prof = profile_id or "workforce"
    base = (
        f"[Error] 编制隔离策略（profile={prof}）要求沙箱执行，但本机无可用隔离后端"
        "——已 fail-closed，**不会**降级到本机裸跑。"
    )
    tips = (
        " 修复建议：Linux 安装 bubblewrap；macOS 确认 sandbox-exec；"
        "Windows 安装 WSL2+bubblewrap，或设置 agent_computer_backend=job（受限模式）。"
        " 权限看板请勿将员工 profile 降为 local/off 以绕过隔离。"
    )
    extra = f" 详情：{detail}" if detail else ""
    cap = f" {capability_note}" if capability_note else ""
    return base + tips + extra + cap
