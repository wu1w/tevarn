"""Adapter layer (syscall surface).

**现状**：HTTP 仍由 ``backend.api`` + ``backend.main`` 承载；本包为 OS 拓扑占位，
避免业务再向更深目录散落。

**目标**：routes 逐步迁入 ``adapters.http``，CLI/MCP 同层并列。

依赖方向：Adapters → Runtime / Kernel（禁止反向）。
"""

from __future__ import annotations

__all__ = ["http"]
