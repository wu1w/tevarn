"""bwrap 沙箱执行后端（Phase 0.5.3 C0.1，Linux）

隔离模型（「敢装清单」基线）：
- workspace 根 rw 绑定（项目内随便写）；
  per-agent HOME（workspace/.computers/<agent_key>/home）rw，互不干扰
- /usr /bin /sbin /lib /lib64 /etc /opt 只读绑定 → 写 /etc、改系统全部失败
- 宿主 HOME 不绑定 → ~/.ssh、~/.aws 等凭证天然不可见
- --clearenv + 最小环境变量（PATH/HOME/LANG/TERM）
- 默认 --unshare-net 断网（agent_computer_network=True 时放开）
- --die-with-parent --new-session：宿主退出沙箱进程随之消亡

cwd 必须落在 workspace 内，否则返回 error（manager 转成清晰错误文案）。
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

from backend.computer.protocol import ExecResult

logger = logging.getLogger(__name__)

_RO_BINDS = ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc", "/opt")


class BwrapBackend:
    backend_id = "bwrap"
    sandboxed = True

    def __init__(
        self,
        workspace_root: str,
        agent_key: str = "main",
        *,
        network: bool = False,
        bwrap_path: str = "bwrap",
    ) -> None:
        self.workspace_root = os.path.abspath(workspace_root)
        self.agent_key = agent_key
        self.network = network
        self.bwrap_path = bwrap_path
        # per-agent HOME：沙箱内可见且可写，agent 之间互不干扰
        self.agent_home = os.path.join(
            self.workspace_root, ".computers", agent_key, "home"
        )

    def _ensure_dirs(self) -> None:
        Path(self.agent_home).mkdir(parents=True, exist_ok=True)

    def _build_argv(self, command: str, cwd: str) -> list[str]:
        argv = [self.bwrap_path, "--die-with-parent", "--new-session"]
        if not self.network:
            argv.append("--unshare-net")
        for d in _RO_BINDS:
            if os.path.isdir(d):
                argv += ["--ro-bind", d, d]
        argv += ["--tmpfs", "/tmp", "--proc", "/proc", "--dev", "/dev"]
        argv += ["--bind", self.workspace_root, self.workspace_root]
        argv += ["--bind", self.agent_home, self.agent_home]
        argv += ["--clearenv"]
        argv += ["--setenv", "HOME", self.agent_home]
        argv += ["--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin"]
        argv += ["--setenv", "LANG", "C.UTF-8"]
        argv += ["--setenv", "TERM", "dumb"]
        argv += ["--chdir", cwd]
        argv += ["--", "/bin/bash", "-lc", command]
        return argv

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
            if rc == 127 and "bwrap:" in err:
                # bwrap 自身启动失败（如 userns 被禁用）→ 明确报错，不静默降级
                return _err(f"bwrap 启动失败（可能 userns 未启用）: {err.strip()[:300]}", 127)
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
            return _err(f"bwrap 未安装或不在 PATH: {self.bwrap_path}", 127)
        except Exception as e:
            return _err(f"沙箱执行异常: {e}")
