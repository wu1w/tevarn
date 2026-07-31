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

        return bool(getattr(settings, "agent_kernel_enabled", True))
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
    await k.mediate(process_id, "tool_call", name, args=arguments or {})


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
        import json as _json

        raw = _json.dumps(arguments or {}, ensure_ascii=False, default=str)
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

    try:
        k = get_kernel()
        await k.mediate(pid, "tool_call", name, args=args)
    except KernelPermissionError as e:
        logger.warning("tool_gate mediate deny tool=%s proc=%s: %s", name, pid[:12], e)
        return args, f"Error: Kernel 权限拒绝——{e}"
    except Exception as e:
        # fail-closed：kernel 故障不得静默放行
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
