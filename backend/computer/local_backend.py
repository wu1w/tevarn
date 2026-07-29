"""local 执行后端（Phase 0.5.3 C0.1）— 现状直跑，无隔离

与 services/tools/executors.py 既有前台执行语义保持一致：
PIPE 收集、超时 kill、max_output 截断、继承宿主环境。
"""
from __future__ import annotations

import time

from backend.computer.protocol import ExecResult


class LocalBackend:
    backend_id = "local"
    sandboxed = False

    def __init__(self, workspace_root: str) -> None:
        self.workspace_root = workspace_root

    async def run(
        self,
        command: str,
        *,
        cwd: str,
        timeout: int = 120,
        max_output: int = 50000,
    ) -> ExecResult:
        t0 = time.monotonic()
        try:
            from backend.core.safe_subprocess import run_capture

            r = await run_capture(
                command,
                cwd=cwd or None,
                timeout=float(timeout),
                max_output=int(max_output),
            )
            if r.get("code") == 124:
                return ExecResult(
                    stdout="",
                    stderr=r.get("stderr") or f"command exceeded {timeout}s and was terminated",
                    exit_code=124,
                    duration_ms=(time.monotonic() - t0) * 1000,
                    backend=self.backend_id,
                    sandboxed=False,
                    error="timeout",
                )
            if str(r.get("stderr") or "").startswith("[Security Blocked]"):
                return ExecResult(
                    stdout="",
                    stderr=str(r.get("stderr") or ""),
                    exit_code=int(r.get("code") or -1),
                    duration_ms=(time.monotonic() - t0) * 1000,
                    backend=self.backend_id,
                    sandboxed=False,
                    error="security",
                )
            return ExecResult(
                stdout=(r.get("stdout") or "").strip(),
                stderr=(r.get("stderr") or "").strip(),
                exit_code=int(r.get("code") if r.get("code") is not None else 0),
                duration_ms=(time.monotonic() - t0) * 1000,
                backend=self.backend_id,
                sandboxed=False,
            )
        except FileNotFoundError:
            return ExecResult(
                stdout="",
                stderr=f"command not found: {command.split()[0] if command else ''}",
                exit_code=127,
                duration_ms=(time.monotonic() - t0) * 1000,
                backend=self.backend_id,
                sandboxed=False,
                error="not_found",
            )
        except Exception as e:
            return ExecResult(
                stdout="",
                stderr=str(e),
                exit_code=1,
                duration_ms=(time.monotonic() - t0) * 1000,
                backend=self.backend_id,
                sandboxed=False,
                error=str(e),
            )
