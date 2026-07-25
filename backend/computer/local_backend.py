"""local 执行后端（Phase 0.5.3 C0.1）— 现状直跑，无隔离

与 services/tools/executors.py 既有前台执行语义保持一致：
PIPE 收集、超时 kill、max_output 截断、继承宿主环境。
"""
from __future__ import annotations

import asyncio
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
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd or None,
            )
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            out = stdout_b.decode("utf-8", errors="replace")
            err = stderr_b.decode("utf-8", errors="replace")
            if len(out) > max_output:
                out = out[:max_output] + f"\n...[stdout truncated {len(stdout_b)} bytes]"
            if len(err) > max_output // 2:
                err = err[: max_output // 2] + f"\n...[stderr truncated {len(stderr_b)} bytes]"
            return ExecResult(
                stdout=out.strip(),
                stderr=err.strip(),
                exit_code=proc.returncode or 0,
                duration_ms=(time.monotonic() - t0) * 1000,
                backend=self.backend_id,
                sandboxed=False,
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()  # type: ignore[union-attr]
                await proc.wait()  # type: ignore[union-attr]
            except Exception:
                pass
            return ExecResult(
                stdout="",
                stderr=f"command exceeded {timeout}s and was terminated",
                exit_code=124,
                duration_ms=(time.monotonic() - t0) * 1000,
                backend=self.backend_id,
                sandboxed=False,
                error="timeout",
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
