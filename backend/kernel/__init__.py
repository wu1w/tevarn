"""Takton Agent Kernel —— 控制平面（阶段 1 / W1 骨架）。

公开接口：
    AgentKernel      进程管理 + 执行中介 + 预算治理 + 审计
    AgentProcess     执行实体（身份/能力/预算/生命周期）
    CapabilityToken  能力令牌（narrowing 单调递减）
    get_kernel()     进程级单例
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
    get_kernel_shared_store,
    reset_kernel_for_tests,
)
from backend.kernel.process import AgentProcess
from backend.kernel.scheduler import AgentScheduler, ScheduledTask

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
    "get_kernel_shared_store",
    "reset_kernel_for_tests",
    "apply_intent_to_process",
    "synthesize_capabilities",
    "synthesize_token",
]
