"""seatbelt 沙箱执行后端（macOS sandbox-exec，2026-07-26）

与 BwrapBackend 同构的隔离模型：
- 读：全系统可读（宿主工具链 /usr /bin /etc 完整可用——这是相对 VM 方案的核心优势）
- 写：仅 workspace + per-agent HOME + /tmp 可写；系统目录/宿主 HOME 不可写
- 宿主 HOME 不可写 → ~/.ssh、~/.aws 等凭证无法被篡改（可读——seatbelt 无法按子路径
  精细排除读取而不破坏系统服务；凭证读取风险由权限控制台 exfiltration 类策略兜底）
- env -i 最小环境变量（PATH/HOME/LANG/TERM）
- 默认断网（agent_computer_network=True 时放开）

依赖：/usr/bin/sandbox-exec（macOS 系统自带；14/15 标记 deprecated 但功能完整）。
cwd 必须落在 workspace 内。
"""
from __future__ import annotations

import asyncio
import logging
import os
import shlex
import time
from pathlib import Path

from backend.computer.protocol import ExecResult

logger = logging.getLogger(__name__)

SANDBOX_EXEC_PATHS = ("/usr/bin/sandbox-exec",)


def find_sandbox_exec() -> str | None:
    """定位 sandbox-exec（系统自带，标准路径；PATH 查找兜底）。"""
    import shutil

    found = shutil.which("sandbox-exec")
    if found:
        return found
    for p in SANDBOX_EXEC_PATHS:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def _sb_escape(path: str) -> str:
    """seatbelt profile 字符串转义（引号/反斜杠）。"""
    return path.replace("\\", "\\\\").replace('"', '\\"')


def _sb_path(path: str) -> str:
    """规范化 seatbelt 路径：保留 POSIX 语义，避免 Windows abspath 改写 /Users/…。

    profile 只在 macOS sandbox-exec 上消费；生成逻辑须在 Win/Linux CI 上可测。
    """
    p = (path or "").strip()
    if not p:
        return p
    # 已是 POSIX 绝对路径（含 macOS /Users /tmp）——勿走宿主 abspath
    if p.startswith("/") and not p.startswith("//"):
        return os.path.normpath(p).replace("\\", "/")
    return os.path.abspath(p)


def build_seatbelt_profile(
    workspace_root: str,
    agent_home: str,
    *,
    network: bool = False,
) -> str:
    """生成 seatbelt profile（deny default + 白名单写）。"""
    ws = _sb_escape(_sb_path(workspace_root))
    home = _sb_escape(_sb_path(agent_home))
    lines = [
        "(version 1)",
        "(deny default)",
        ";; 读：全系统（宿主工具链需要 /usr /bin /etc /Library）",
        "(allow file-read*)",
        ";; 写：仅 workspace / agent HOME / tmp",
        "(allow file-write*",
        f'  (subpath "{ws}")',
        f'  (subpath "{home}")',
        '  (subpath "/tmp")',
        '  (subpath "/private/tmp")',
        '  (literal "/dev/null")',
        '  (regex #"^/dev/tty.*")',
        ")",
        ";; 进程执行/创建",
        "(allow process-exec process-fork)",
        ";; dyld/系统服务基本 mach 通道",
        "(allow mach-lookup",
        '  (global-name "com.apple.system.opendirectoryd.libinfo")',
        '  (global-name "com.apple.system.logger")',
        ")",
        "(allow sysctl-read)",
        "(allow signal (target self))",
    ]
    if network:
        lines.append(";; 网络放开（agent_computer_network=True）")
        lines.append("(allow network*)")
    else:
        lines.append(";; 默认断网（仅允许本机 loopback 通信）")
        lines.append("(allow network* (local ip))")
    return "\n".join(lines) + "\n"


class SeatbeltBackend:
    backend_id = "seatbelt"
    sandboxed = True

    def __init__(
        self,
        workspace_root: str,
        agent_key: str = "main",
        *,
        network: bool = False,
        sandbox_exec_path: str | None = None,
    ) -> None:
        self.workspace_root = os.path.abspath(workspace_root)
        self.agent_key = agent_key
        self.network = network
        self.sandbox_exec_path = sandbox_exec_path or find_sandbox_exec() or "sandbox-exec"
        # per-agent HOME：沙箱内可见且可写，agent 之间互不干扰
        from backend.computer.pathutil import sanitize_agent_key_for_path

        safe_key = sanitize_agent_key_for_path(agent_key)
        self.agent_home = os.path.join(
            self.workspace_root, ".computers", safe_key, "home"
        )

    def _ensure_dirs(self) -> None:
        Path(self.agent_home).mkdir(parents=True, exist_ok=True)

    def _build_argv(self, command: str, cwd: str) -> list[str]:
        profile = build_seatbelt_profile(
            self.workspace_root, self.agent_home, network=self.network
        )
        # sandbox-exec 无 --chdir：包一层 cd；env -i 模拟 clearenv
        wrapped = f"cd {shlex.quote(cwd)} && {command}"
        return [
            self.sandbox_exec_path,
            "-p",
            profile,
            "/usr/bin/env",
            "-i",
            f"HOME={self.agent_home}",
            "PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "LANG=C.UTF-8",
            "TERM=dumb",
            "/bin/bash",
            "-lc",
            wrapped,
        ]

    def _check_cwd(self, cwd: str) -> str | None:
        """cwd 必须在 workspace 内；返回 None 表示合法，否则返回错误文案"""
        real = os.path.abspath(cwd)
        root = self.workspace_root
        if real == root or real.startswith(root + os.sep):
            return None
        return f"cwd 超出沙箱 workspace（{root}）: {real}"

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

        argv = self._build_argv(command, os.path.abspath(cwd))
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
            return _err(
                f"sandbox-exec 不可用（非 macOS 或已被移除）: {self.sandbox_exec_path}", 127
            )
        except Exception as e:
            return _err(f"沙箱执行异常: {e}")
