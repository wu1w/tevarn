"""Runtime 层：Kernel-first 宿主 + 编制编排门面。

- 入口：``python -m backend.runtime``（见 ``__main__.py``）
- 编排门面：``backend.runtime.facade``（Identity/Inbox/Dispatcher 访问点）
- 实现体仍在 ``backend.kernel.*``（绞杀者迁移，非一次搬空）
"""

from __future__ import annotations

from backend.runtime.facade import (
    build_daily_report,
    get_kernel,
    get_workforce_dispatcher,
    get_workforce_inbox,
    init_workforce,
)

__all__ = [
    "build_daily_report",
    "get_kernel",
    "get_workforce_dispatcher",
    "get_workforce_inbox",
    "init_workforce",
]
