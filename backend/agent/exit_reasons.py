"""P0.5 R4: structured loop exit reasons + recovery UX copy.

Maps internal ``last_exit_reason`` codes to user-facing Chinese messages,
recovery hints, and API-friendly payloads for control console / resume entry.
"""

from __future__ import annotations

from typing import Any

# code → (title, body, recovery_hint, severity)
_EXIT_CATALOG: dict[str, tuple[str, str, str, str]] = {
    "budget_grace": (
        "迭代预算已用尽（宽限终答）",
        "本段迭代配额已满，系统已强制生成最终回复，未再调用工具。",
        "可提高 agent_max_iterations / 自动续段，或拆小任务后重试；"
        "进程若仍 suspended 可在控制台「恢复」。",
        "warn",
    ),
    "budget_exhausted": (
        "迭代预算耗尽",
        "运行在耗尽迭代配额后停止，未能进入宽限终答。",
        "提高迭代上限或缩小目标后重开会话；检查 Goal 自动续段设置。",
        "error",
    ),
    "kernel_iteration_exhausted": (
        "内核迭代预算耗尽",
        "Rust policy 侧 iteration 预算已耗尽，已触发优雅收尾。",
        "在控制台查看 /kernel/policy/{process_id}；必要时 resume 或新开 run。",
        "warn",
    ),
    "kernel_budget_precheck": (
        "Token 预算不足（事前中断）",
        "进程 token 预算不足以支撑下一次 LLM 调用，已避免烧穿预算。",
        "提高进程 token_budget / top_up，或收窄任务范围后重试。",
        "error",
    ),
    "kernel_gate_stop": (
        "内核门闩停止",
        "运行被 kernel 仲裁门（stop/挂起超时）中断。",
        "若进程为 suspended：POST /kernel/processes/{id}/resume 恢复；"
        "否则查看 decision_trail。",
        "info",
    ),
    "doom_loop": (
        "工具空转熔断（doom loop）",
        "同一工具与相近参数连续重复，系统已熔断并改为直接作答。",
        "换参数/换工具，或人工指定下一步；可在权限中调整 doom_loop 策略。",
        "warn",
    ),
    "thrash": (
        "工具空转熔断",
        "检测到重复工具调用，已停止工具轮次。",
        "根据已有结果作答，或换一种工具路径；避免同一命令连点。",
        "warn",
    ),
    "stopped_by_user": (
        "用户停止",
        "运行被用户手动停止。",
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
        "重建：cargo build -p takton-kernel-host --release。",
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


def describe_exit_reason(code: str | None) -> dict[str, Any]:
    """Return structured UX payload for a loop exit code."""
    c = (code or "").strip() or "completed"
    if c in _EXIT_CATALOG:
        title, body, hint, sev = _EXIT_CATALOG[c]
    else:
        title, body, hint, sev = (
            f"结束原因：{c}",
            f"运行以代码 {c} 结束。",
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


def format_exit_user_message(code: str | None, *, process_id: str | None = None) -> str:
    d = describe_exit_reason(code)
    pid = (process_id or "").strip()
    lines = [
        f"[{d['title']}]",
        d["message"],
    ]
    if d.get("recovery_hint"):
        lines.append(f"恢复建议：{d['recovery_hint']}")
    if pid:
        lines.append(f"process_id={pid} · 控制台可 resume / 查看策略与决策轨迹。")
    return "\n".join(lines)


__all__ = ["describe_exit_reason", "format_exit_user_message"]
