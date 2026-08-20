"""P0.5 R4: structured loop exit reasons + recovery UX copy.

Maps internal ``last_exit_reason`` codes to user-facing Chinese messages,
recovery hints, and API-friendly payloads for control console / resume entry.
"""

from __future__ import annotations

from typing import Any

# code → (title, body, recovery_hint, severity)
_EXIT_CATALOG: dict[str, tuple[str, str, str, str]] = {
    "budget_grace": (
        "本轮步数已用完",
        "本轮步数已经用完，已根据现有结果给出答复。",
        "可提高 agent_max_iterations / 自动续段，或拆小任务后重试；"
        "进程若仍 suspended 可在控制台「恢复」。",
        "warn",
    ),
    "budget_exhausted": (
        "本轮步数已用完",
        "本轮步数已经用完，没有再继续调用工具。",
        "提高迭代上限或缩小目标后重开会话；检查 Goal 自动续段设置。",
        "error",
    ),
    # Token process budget (charge_tokens) — MUST NOT reuse budget_exhausted title
    "kernel_token_budget_exhausted": (
        "额度已用完",
        "这一轮的用量额度已经用完，所以停在这里。",
        "主会话/CEO：系统会自动 chat_elastic top_up（上限 agent_chat_budget_hard_cap）；"
        "若仍中断多半已达天花板或 host 不可用。编制工单：crew_steward top_up / 提高 token_budget。",
        "error",
    ),
    "kernel_iteration_exhausted": (
        "本轮步数已用完",
        "本轮步数已经用完，已根据现有结果收束。",
        "在控制台查看 /kernel/policy/{process_id}；必要时 resume 或新开 run。",
        "warn",
    ),
    "kernel_budget_precheck": (
        "额度不足",
        "剩余额度不够支撑下一轮，已提前停下以免空转。",
        "提高进程 token_budget / top_up，或收窄任务范围后重试。",
        "error",
    ),
    "kernel_gate_stop": (
        "运行已中断",
        "这一轮被系统中断了。",
        "若进程为 suspended：POST /kernel/processes/{id}/resume 恢复；"
        "否则查看 decision_trail。",
        "info",
    ),
    "doom_loop": (
        "已停止重复操作",
        "同样的步骤重复了多次，已改为直接作答。",
        "换参数/换工具，或人工指定下一步；可在权限中调整 doom_loop 策略。",
        "warn",
    ),
    "thrash": (
        "已停止重复操作",
        "检测到重复操作，已停止继续调用工具。",
        "根据已有结果作答，或换一种工具路径；避免同一命令连点。",
        "warn",
    ),
    "empty_content_thrash": (
        "没有生成回复",
        "这一轮没有生成可见回复。直接发送下一条即可。",
        "重试同一问题，或换一种说法。",
        "warn",
    ),
    "llm_visible_idle": (
        "思考时间过长",
        "模型思考时间过长，本轮先停。发送「继续」可接着做。",
        "可缩小上下文或拆小任务后重试。",
        "warn",
    ),
    "max_tool_rounds": (
        "本轮步数已用完",
        "本轮步数已经用完，已根据现有结果给出答复。",
        "发送「请继续」或缩小任务后重试。",
        "warn",
    ),
    "max_segment_budget": (
        "本轮步数已用完",
        "本轮步数已经用完，已根据现有结果给出答复。",
        "发送「请继续」可接着做。",
        "warn",
    ),
    "stopped_by_user": (
        "已停止",
        "已按你的操作停止生成。",
        "可重新发送消息继续；挂起进程可用 resume。",
        "info",
    ),
    "completed": (
        "正常完成",
        "运行已正常结束。",
        "",
        "ok",
    ),
    "host_down": (
        "Kernel Host 不可用",
        "Rust 控制平面无响应或未启动，工具与进程治理暂时不可用。",
        "点击「重启 Host」或执行 scripts/ensure-vendor-host；"
        "重建：cargo build -p tevarn-kernel-host --release。",
        "error",
    ),
    "host_abi_mismatch": (
        "Kernel ABI 不完整",
        "Host 在线但缺少必需 ABI 方法，已 fail-closed 禁止半跑。",
        "重建并 stage host：.\\scripts\\build-kernel-host.ps1 -Release "
        "或 node scripts/ensure-vendor-host.mjs。",
        "error",
    ),
    "sandbox_missing": (
        "沙箱能力不可用",
        "当前平台缺少 Job/bwrap 等隔离后端，workforce 默认 fail-closed。",
        "安装 bubblewrap（Linux）或启用 WSL/Job；"
        "开发可临时设 agent_execution_mode=local（削弱治理）。",
        "error",
    ),
    "intent_denied": (
        "Intent / 能力被拒绝",
        "本次 Run 的 Intent 合成或能力申请未通过（最小权限）。",
        "缩小目标、补充 capabilities，或在 /approvals 批准提权后重试。",
        "warn",
    ),
    "resource_denied": (
        "资源配额拒绝",
        "tool_calls / child_proc / io / memory 等资源账户超限。",
        "在 Kernel 页查看 resource 用量；降低并发或 top_up 预算后重试。",
        "error",
    ),
}


# Chat-channel next step (no API paths / process ids — matches Cursor/ChatGPT tone)
_CHAT_NEXT: dict[str, str] = {
    "budget_grace": "需要的话，发送「请继续」。",
    "budget_exhausted": "发送「请继续」可以接着做。",
    "kernel_iteration_exhausted": "发送「请继续」可以接着做。",
    "kernel_token_budget_exhausted": "缩小范围后再试，或在设置里提高上限。",
    "kernel_budget_precheck": "缩小任务后再试。",
    "kernel_gate_stop": "发送新消息即可继续。",
    "doom_loop": "若还要改，直接说下一步即可。",
    "thrash": "若还要改，直接说下一步即可。",
    "stopped_by_user": "",
    "completed": "",
    "empty_content_thrash": "",
    "max_tool_rounds": "发送「请继续」可以接着做。",
    "max_segment_budget": "发送「请继续」可以接着做。",
    "goal_stalled": "目标一段时间没有推进。直接说下一步，或发送「请继续」。",
}

# Overlay when catalog body still names kernel/host internals
_CHAT_BODY: dict[str, str] = {
    "host_down": "系统暂时连不上，请稍后再试。",
    "host_abi_mismatch": "系统组件需要更新后才能继续。",
    "sandbox_missing": "当前环境无法安全执行这项操作。",
    "intent_denied": "这次操作没有获得所需权限。",
    "resource_denied": "用量达到上限，这一轮先停在这里。",
    "empty_content_thrash": "这一轮没有生成可见回复。直接发送下一条即可。",
}


def describe_exit_reason(code: str | None) -> dict[str, Any]:
    """Return structured UX payload for a loop exit code (API / console)."""
    c = (code or "").strip() or "completed"
    if c in _EXIT_CATALOG:
        title, body, hint, sev = _EXIT_CATALOG[c]
    else:
        title, body, hint, sev = (
            "这一轮已结束",
            "这一轮已经结束。",
            "查看 decision_trail 与 process meta；必要时 resume 或新开 run。",
            "info",
        )
    return {
        "code": c,
        "title": title,
        "message": body,
        "recovery_hint": hint,
        "severity": sev,
        "resume_entry": "/api/kernel/processes/{process_id}/resume",
        "policy_entry": "/api/kernel/policy/{process_id}",
        "trail_entry": "/api/kernel/decision_trail/{process_id}",
        "cost_entry": "/api/kernel/cost",
    }


def format_exit_user_message(
    code: str | None,
    *,
    process_id: str | None = None,
    for_operator: bool = False,
) -> str:
    """User-chat copy by default. Operator/console keeps API hints when requested."""
    d = describe_exit_reason(code)
    if for_operator:
        pid = (process_id or "").strip()
        lines = [f"[{d['title']}]", d["message"]]
        if d.get("recovery_hint"):
            lines.append(f"恢复建议：{d['recovery_hint']}")
        if pid:
            lines.append(f"process_id={pid} · 控制台可 resume / 查看策略与决策轨迹。")
        return "\n".join(lines)
    c = str(d.get("code") or "")
    body = _CHAT_BODY.get(c) or str(d.get("message") or "").strip()
    lines = [body]
    nxt = _CHAT_NEXT.get(c, "")
    if nxt:
        lines.append(nxt)
    return "\n".join(x for x in lines if x)


__all__ = ["describe_exit_reason", "format_exit_user_message"]
