"""工作方式 / 执行环境 —— 权限体系的单一事实源（T5）。

此前权限口径散在三处且互相不知道对方存在：
  1. `permissions_rules.PermissionGate`（profile + 规则，Claude Code 风格）
  2. `tools.permissions.ToolPermissionManager`（路径边界 + 一个从未被调用的
     `needs_confirmation`）
  3. `core.command_policy`（高危命令按类别三态）
再叠加 `agent_permission_ask_mode="local_allow"` 把所有 ask 静默降级为 allow，
结果是「权限系统看起来很完整，实际默认全放行」。

这里把用户真正关心的两个正交决定显式化，各自只有一个开关：

  · **工作方式**（agent_working_mode）—— agent 动作要不要经我同意
  · **执行环境**（agent_execution_mode）—— 命令跑在沙箱里还是本机上

底层的 profile / ask_mode / 沙箱后端都由这两个派生，UI 只呈现这两个。
高级用户仍可单独覆盖底层键（覆盖后 describe_current() 会标记 custom）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

WorkingMode = Literal["readonly", "cautious", "auto_edit", "autonomous"]
ExecutionMode = Literal["sandbox", "auto", "local"]

DEFAULT_WORKING_MODE: WorkingMode = "cautious"
DEFAULT_EXECUTION_MODE: ExecutionMode = "auto"


@dataclass(frozen=True)
class WorkingModeSpec:
    id: str
    label: str
    label_en: str
    desc: str
    desc_en: str
    profile: str  # → permissions_rules.rules_for_profile
    ask_mode: str  # auto | interactive | local_allow | deny
    recommended: bool = False


# 顺序 = UI 展示顺序（从最保守到最放手）
WORKING_MODES: tuple[WorkingModeSpec, ...] = (
    WorkingModeSpec(
        id="readonly",
        label="只读探索",
        label_en="Read-only",
        desc="只看不改：读文件、搜索、上网都可以，写文件和执行命令一律拒绝。适合先摸清代码再动手。",
        desc_en="Look, don't touch. Reads and searches are allowed; writes and shell commands are refused.",
        profile="plan",
        ask_mode="auto",
    ),
    WorkingModeSpec(
        id="cautious",
        label="谨慎",
        label_en="Cautious",
        desc="改文件、跑命令前都问我一次。最安全，但长任务会被频繁打断。",
        desc_en="Ask before every file write and shell command. Safest, but interrupts long tasks often.",
        profile="cautious",
        ask_mode="auto",
        recommended=True,
    ),
    WorkingModeSpec(
        id="auto_edit",
        label="自动编辑",
        label_en="Auto-edit",
        desc="工作区内改文件不打扰我，执行命令仍然要问。日常写代码推荐这档。",
        desc_en="File edits inside the workspace run freely; shell commands still ask. Best for everyday coding.",
        profile="acceptEdits",
        ask_mode="auto",
    ),
    WorkingModeSpec(
        id="autonomous",
        label="全自动",
        label_en="Autonomous",
        desc="全程不打断，安全边界完全交给沙箱。建议配合「沙箱」执行环境使用。",
        desc_en="Never interrupts; the sandbox is the only boundary. Pair this with sandboxed execution.",
        profile="free",
        ask_mode="local_allow",
    ),
)

WORKING_MODE_BY_ID: dict[str, WorkingModeSpec] = {m.id: m for m in WORKING_MODES}


@dataclass(frozen=True)
class ExecutionModeSpec:
    id: str
    label: str
    label_en: str
    desc: str
    desc_en: str
    recommended: bool = False


EXECUTION_MODES: tuple[ExecutionModeSpec, ...] = (
    ExecutionModeSpec(
        id="sandbox",
        label="沙箱（强制）",
        label_en="Sandbox (required)",
        desc="命令只在隔离环境里跑。本机没有可用沙箱时直接报错，绝不偷偷退回本机。",
        desc_en="Commands only run isolated. If no sandbox is available it errors out instead of silently falling back.",
    ),
    ExecutionModeSpec(
        id="auto",
        label="自动",
        label_en="Auto",
        desc="有沙箱就用沙箱，没有就本机直跑并在这里明确告知你。",
        desc_en="Uses the sandbox when available, otherwise runs locally and says so here.",
        recommended=True,
    ),
    ExecutionModeSpec(
        id="local",
        label="本机直跑",
        label_en="Local",
        desc="命令直接在你的机器上执行，没有隔离。只在你完全信任任务内容时使用。",
        desc_en="Commands run directly on your machine with no isolation. Only for fully trusted work.",
    ),
)

EXECUTION_MODE_BY_ID: dict[str, ExecutionModeSpec] = {m.id: m for m in EXECUTION_MODES}


def _settings() -> Any:
    from backend.core.config import settings

    return settings


def resolve_working_mode(raw: str | None = None) -> WorkingModeSpec:
    """当前工作方式；非法值回落到默认（谨慎），绝不静默放宽。"""
    if raw is None:
        raw = getattr(_settings(), "agent_working_mode", DEFAULT_WORKING_MODE)
    key = str(raw or "").strip().lower()
    return WORKING_MODE_BY_ID.get(key) or WORKING_MODE_BY_ID[DEFAULT_WORKING_MODE]


def resolve_execution_mode(raw: str | None = None) -> str:
    if raw is None:
        raw = getattr(_settings(), "agent_execution_mode", DEFAULT_EXECUTION_MODE)
    key = str(raw or "").strip().lower()
    return key if key in EXECUTION_MODE_BY_ID else DEFAULT_EXECUTION_MODE


def effective_permission_profile() -> str:
    """底层 profile：优先显式覆盖，否则由工作方式派生。"""
    override = str(getattr(_settings(), "agent_permission_profile", "") or "").strip()
    if override and override.lower() != "auto":
        return override
    return resolve_working_mode().profile


def effective_ask_mode() -> str:
    """底层 ask_mode：优先显式覆盖，否则由工作方式派生。"""
    override = str(getattr(_settings(), "agent_permission_ask_mode", "") or "").strip()
    if override and override.lower() != "auto":
        return override
    return resolve_working_mode().ask_mode


@dataclass
class SandboxDecision:
    """执行环境的最终裁决。"""

    use_sandbox: bool
    mode: str  # 用户选择的 execution mode
    capability_mode: str  # detect 探测到的沙箱后端（none 表示没有）
    capability_label: str
    reason: str = ""
    # auto 模式下无沙箱可用而退回本机 —— 必须让用户看见，不能悄悄降级
    degraded: bool = False


def decide_sandbox() -> SandboxDecision:
    """按执行环境 + 平台能力裁决本次命令是否走沙箱。

    三档语义刻意不同：
    - sandbox：必须隔离。没有沙箱 → use_sandbox=True，由执行层报错（不降级）
    - auto   ：有则用，无则本机 + degraded=True（UI 明示）
    - local  ：显式本机
    """
    mode = resolve_execution_mode()
    try:
        from backend.computer.detect import detect_sandbox_capability

        cap = detect_sandbox_capability()
    except Exception as e:  # 探测本身失败：按无沙箱处理
        return SandboxDecision(
            use_sandbox=(mode == "sandbox"),
            mode=mode,
            capability_mode="none",
            capability_label="未知",
            reason=f"沙箱能力探测失败: {e}",
            degraded=(mode == "auto"),
        )

    if mode == "local":
        return SandboxDecision(
            use_sandbox=False,
            mode=mode,
            capability_mode=cap.mode,
            capability_label=cap.label,
            reason="已选择本机直跑",
        )

    if cap.available:
        return SandboxDecision(
            use_sandbox=True,
            mode=mode,
            capability_mode=cap.mode,
            capability_label=cap.label,
            reason=cap.note or "",
        )

    if mode == "sandbox":
        # 强制沙箱：不可用就是不可用，交给执行层报错
        return SandboxDecision(
            use_sandbox=True,
            mode=mode,
            capability_mode=cap.mode,
            capability_label=cap.label,
            reason=cap.note or "本机无可用沙箱方案",
        )

    return SandboxDecision(
        use_sandbox=False,
        mode=mode,
        capability_mode=cap.mode,
        capability_label=cap.label,
        reason=cap.note or "本机无可用沙箱方案，已退回本机直跑",
        degraded=True,
    )


def describe_current() -> dict[str, Any]:
    """权限控制台用：当前选择 + 实际生效值 + 可选项目录。"""
    s = _settings()
    wm = resolve_working_mode()
    sandbox = decide_sandbox()
    profile_override = str(getattr(s, "agent_permission_profile", "") or "").strip()
    ask_override = str(getattr(s, "agent_permission_ask_mode", "") or "").strip()

    return {
        "working_mode": wm.id,
        "execution_mode": sandbox.mode,
        "effective": {
            "permission_profile": effective_permission_profile(),
            "ask_mode": effective_ask_mode(),
            "permission_enabled": bool(
                getattr(s, "agent_permission_enabled", True)
            ),
            "headless_fallback": str(
                getattr(s, "agent_permission_headless", "allow") or "allow"
            ),
            "use_sandbox": sandbox.use_sandbox,
            "sandbox_backend": sandbox.capability_mode,
            "sandbox_label": sandbox.capability_label,
            "sandbox_degraded": sandbox.degraded,
            "sandbox_reason": sandbox.reason,
        },
        # 高级用户单独改了底层键时标出来，避免 UI 与实际不符还无从察觉
        "overrides": {
            "permission_profile": profile_override
            if profile_override and profile_override.lower() != "auto"
            else None,
            "ask_mode": ask_override
            if ask_override and ask_override.lower() != "auto"
            else None,
        },
        "working_modes": [
            {
                "id": m.id,
                "label": m.label,
                "label_en": m.label_en,
                "desc": m.desc,
                "desc_en": m.desc_en,
                "profile": m.profile,
                "recommended": m.recommended,
            }
            for m in WORKING_MODES
        ],
        "execution_modes": [
            {
                "id": m.id,
                "label": m.label,
                "label_en": m.label_en,
                "desc": m.desc,
                "desc_en": m.desc_en,
                "recommended": m.recommended,
                # 强制沙箱在本机不可用时要能置灰并说明原因
                "available": (m.id != "sandbox") or sandbox.capability_mode != "none",
            }
            for m in EXECUTION_MODES
        ],
    }
