"""Rust Kernel / Runtime adapter for Tevarn.

Provides an ``AgentKernel``-compatible façade that talks to
``tevarn-kernel-host`` over JSON-RPC (TCP line protocol).

Environment:
  TEVARN_KERNEL_BACKEND=rust|python   (default: rust if host reachable, else python)
  TEVARN_KERNEL_HOST=127.0.0.1:17890
  TEVARN_KERNEL_HOST_BIN=path/to/tevarn-kernel-host.exe
  TEVARN_KERNEL_AUTO_START=1         (default 1 — spawn host if not running)
"""

from __future__ import annotations

from backend.kernel_rust.client import (
    BudgetExceededError,
    CapabilityEscalationError,
    KernelPermissionError,
    RustAgentKernel,
    RustKernelProcess,
    get_rust_kernel,
    is_rust_host_available,
    reset_rust_kernel_for_tests,
    start_kernel_host,
)

__all__ = [
    "BudgetExceededError",
    "CapabilityEscalationError",
    "KernelPermissionError",
    "RustAgentKernel",
    "RustKernelProcess",
    "get_rust_kernel",
    "is_rust_host_available",
    "reset_rust_kernel_for_tests",
    "start_kernel_host",
]
