"""Tevarn Agent Kernel —— 控制平面公开接口。

P0-A：生产默认 **Rust host**（``get_kernel()`` → ``kernel_rust``）。
Python 内 ``AgentKernel`` / ``AgentProcess`` 等仅为单测与 fallback。

ABI：``docs/kernel-abi-v1.md`` · 实现：``crates/tevarn-kernel``
"""

from backend.kernel.capability import CapabilityEscalationError, CapabilityToken
from backend.kernel.intent import (
    DEFAULT_GRANTABLE,
    RISKY_CAPABILITIES,
    IntentDeclaration,
    apply_intent_to_process,
    synthesize_capabilities,
    synthesize_token,
)
from backend.kernel.kernel import (
    AgentKernel,
    BudgetExceededError,
    KernelEvent,
    KernelPermissionError,
    MediationDecision,
    get_kernel,
    get_kernel_backend,
    get_kernel_shared_store,
    reset_kernel_for_tests,
)
from backend.kernel.process import AgentProcess
from backend.kernel.scheduler import AgentScheduler, ScheduledTask
from backend.kernel.tool_gate import (
    enforce_tool_gate,
    extract_process_id,
    workforce_sandbox_fail_message,
)

__all__ = [
    "AgentKernel",
    "AgentProcess",
    "AgentScheduler",
    "BudgetExceededError",
    "CapabilityEscalationError",
    "CapabilityToken",
    "DEFAULT_GRANTABLE",
    "IntentDeclaration",
    "KernelEvent",
    "KernelPermissionError",
    "MediationDecision",
    "RISKY_CAPABILITIES",
    "ScheduledTask",
    "get_kernel",
    "get_kernel_backend",
    "get_kernel_shared_store",
    "reset_kernel_for_tests",
    "apply_intent_to_process",
    "synthesize_capabilities",
    "synthesize_token",
    "enforce_tool_gate",
    "extract_process_id",
    "workforce_sandbox_fail_message",
]
