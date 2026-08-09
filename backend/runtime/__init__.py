"""Runtime 层：Kernel-first 宿主 + 编制编排门面。

- 控制平面：**Rust** ``tevarn-kernel`` / ``tevarn-kernel-host``
- 入口：``python -m backend.runtime``（HTTP Adapter）或 ``tevarn-kernel-host``
- 编排门面：``backend.runtime.facade``（Identity/Inbox/Dispatcher 访问点）
- Workforce 业务适配仍在 ``backend.kernel.*``（DB/Agent Loop 胶水）
"""

from __future__ import annotations

from backend.runtime.facade import (
    build_daily_report,
    get_kernel,
    get_kernel_backend,
    get_workforce_dispatcher,
    get_workforce_inbox,
    init_workforce,
    runtime_health,
)

__all__ = [
    "build_daily_report",
    "get_kernel",
    "get_kernel_backend",
    "get_workforce_dispatcher",
    "get_workforce_inbox",
    "init_workforce",
    "runtime_health",
]
