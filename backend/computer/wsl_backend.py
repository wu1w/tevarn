"""WSL bwrap 沙箱执行后端（Windows + WSL2，2026-07-26）

思路：检测到 WSL2 且其内装有 bubblewrap 时，把命令经 wsl.exe 转发到
Linux 侧 bwrap 执行——完整复用 BwrapBackend 的隔离语义
（ro-bind / clearenv / unshare-net / die-with-parent）。

工具链边界（诚实声明）：沙箱内是 **WSL 的 Linux 环境**——Windows 宿主的
node.exe/python.exe 不可用；workspace 经 /mnt/<drive>/ 映射可读写。
适合运行不受信任代码；日常开发命令建议使用者的工具链装在 WSL 内。

ctypes/平台调用延迟到执行期，本模块在非 Windows 上可安全 import（测试需要）。
"""
from __future__ import annotations

import asyncio
import logging
import ntpath
import os
import re
import shutil
import time
from pathlib import Path

from backend.computer.protocol import ExecResult

logger = logging.getLogger(__name__)

_DRIVE_RE = re.compile(r"^([A-Za-z]):[\\/]")


def win_path_to_wsl(path: str) -> str:
    """C:\\foo\\bar → /mnt/c/foo/bar（UNC/非盘符路径原样返回）。"""
    p = path.replace("/", "\\")
    m = _DRIVE_RE.match(p)
    if not m:
        return p.replace("\\", "/")
    drive = m.group(1).lower()
    rest = p[2:].lstrip("\\").replace("\\", "/")
    return f"/mnt/{drive}/{rest}"


def find_wsl() -> str | None:
    """定位 wsl.exe。"""
    return shutil.which("wsl.exe") or shutil.which("wsl")


def wsl_has_bwrap(wsl_path: str | None = None) -> bool:
    """WSL 默认发行版内 bwrap 可用性（同步探测，带短超时）。"""
    import subprocess

    wsl = wsl_path or find_wsl()
    if not wsl:
        return False
    try:
        proc = subprocess.run(
            [wsl, "-e", "bash", "-lc", "command -v bwrap"],
            capture_output=True,
            timeout=8,
        )
        return proc.returncode == 0 and b"bwrap" in proc.stdout
    except Exception:
        return False


_RO_BINDS = ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc", "/opt")


class WslBwrapBackend:
    """Windows 宿主编排 + WSL 内 bwrap 执行。"""

    backend_id = "wsl-bwrap"
    sandboxed = True

    def __init__(
        self,
        workspace_root: str,
        agent_key: str = "main",
        *,
        network: bool = False,
        wsl_path: str | None = None,
    ) -> None:
        # Windows 路径统一走 ntpath（代码在 Linux 上被测试时 os.path 语义是错的）
        self.workspace_root = ntpath.abspath(workspace_root)
        self.agent_key = agent_key
        self.network = network
        self.wsl_path = wsl_path or find_wsl() or "wsl.exe"
        self.agent_home = os.path.join(
            self.workspace_root, ".computers", agent_key, "home"
        )

    def _ensure_dirs(self) -> None:
        Path(self.agent_home).mkdir(parents=True, exist_ok=True)

    def _check_cwd(self, cwd: str) -> str | None:
        real = ntpath.abspath(cwd)
        root = self.workspace_root
        if real == root or real.startswith(root + ntpath.sep):
            return None
        return f"cwd 超出沙箱 workspace（{root}）: {real}"

    def _build_argv(self, command: str, cwd: str) -> list[str]:
        ws_wsl = win_path_to_wsl(self.workspace_root)
        home_wsl = win_path_to_wsl(self.agent_home)
        cwd_wsl = win_path_to_wsl(cwd)
        argv = [self.wsl_path, "-e", "bwrap", "--die-with-parent", "--new-session"]
        if not self.network:
            argv.append("--unshare-net")
        for d in _RO_BINDS:
            argv += ["--ro-bind", d, d]
        argv += ["--tmpfs", "/tmp", "--proc", "/proc", "--dev", "/dev"]
        argv += ["--bind", ws_wsl, ws_wsl]
        argv += ["--bind", home_wsl, home_wsl]
        argv += ["--clearenv"]
        argv += ["--setenv", "HOME", home_wsl]
        argv += ["--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin"]
        argv += ["--setenv", "LANG", "C.UTF-8"]
        argv += ["--setenv", "TERM", "dumb"]
        argv += ["--chdir", cwd_wsl]
        argv += ["--", "/bin/bash", "-lc", command]
        return argv

    async def run(
        self,
        command: str,
        *,
        cwd: str,
        timeout: int = 120,
        max_output: int = 50000,
    ) -> ExecResult:
        t0 = time.monotonic()

        def _err(msg: str, code: int = 1) -> ExecResult:
            return ExecResult(
                stdout="",
                stderr=msg,
                exit_code=code,
                duration_ms=(time.monotonic() - t0) * 1000,
                backend=self.backend_id,
                sandboxed=True,
                error=msg,
            )

        cwd_err = self._check_cwd(cwd)
        if cwd_err:
            return _err(cwd_err, code=2)

        try:
            self._ensure_dirs()
        except Exception as e:
            return _err(f"沙箱 HOME 创建失败: {e}")

        argv = self._build_argv(command, ntpath.abspath(cwd))
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            out = stdout_b.decode("utf-8", errors="replace")
            err = stderr_b.decode("utf-8", errors="replace")
            rc = proc.returncode or 0
            if len(out) > max_output:
                out = out[:max_output] + f"\n...[stdout truncated {len(stdout_b)} bytes]"
            if len(err) > max_output // 2:
                err = err[: max_output // 2] + f"\n...[stderr truncated {len(stderr_b)} bytes]"
            return ExecResult(
                stdout=out.strip(),
                stderr=err.strip(),
                exit_code=rc,
                duration_ms=(time.monotonic() - t0) * 1000,
                backend=self.backend_id,
                sandboxed=True,
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()  # type: ignore[union-attr]
                await proc.wait()  # type: ignore[union-attr]
            except Exception:
                pass
            return _err(f"command exceeded {timeout}s and was terminated", 124)
        except FileNotFoundError:
            return _err(f"wsl.exe 不可用: {self.wsl_path}", 127)
        except Exception as e:
            return _err(f"WSL 沙箱执行异常: {e}")
