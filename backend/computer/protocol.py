"""ExecutionBackend 协议（Phase 0.5.3 C0.1）

command/python 工具统一经此后端执行；实现：local（现状）/ bwrap（Linux 沙箱）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class ExecResult:
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: float
    backend: str          # "local" | "bwrap"
    sandboxed: bool
    error: str | None = None  # 启动级错误（后端不可用 / cwd 越界等）

    @property
    def ok(self) -> bool:
        return self.error is None and self.exit_code == 0


class ExecutionBackend(Protocol):
    """命令执行后端协议"""

    backend_id: str
    sandboxed: bool

    async def run(
        self,
        command: str,
        *,
        cwd: str,
        timeout: int = 120,
        max_output: int = 50000,
    ) -> ExecResult:
        """执行 shell 命令。cwd 必须在后端允许的根内，否则返回 error。"""
        ...
