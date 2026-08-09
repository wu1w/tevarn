"""ssh 执行后端（P2a，2026-07-29 夜间路线图）

agent 命令在远程主机执行——「agent 活在 $5 VPS 上，人从本地/IM 遥控」。
对标 Hermes 的 SSH terminal backend。

设计：
- 依赖系统 ssh 客户端 + 免密（key/agent），不管理凭据（红线：不存私钥/密码）
- 远端工作目录固定为 agent_computer_ssh_workdir（默认 ~/tevarn-ws），
  每个 agent_key 一个子目录，首次执行时 mkdir -p
- BatchMode=yes：任何交互式认证请求直接失败，绝不挂死 loop
- sandboxed=True 语义：命令不在宿主执行（宿主视角是隔离的）；
  远端本身的安全边界由用户的 VPS 负责
"""
from __future__ import annotations

import asyncio
import logging
import shlex
import shutil
import time

from backend.computer.pathutil import sanitize_agent_key_for_path
from backend.computer.protocol import ExecResult

logger = logging.getLogger(__name__)


def ssh_available() -> bool:
    return shutil.which("ssh") is not None


class SshBackend:
    backend_id = "ssh"
    sandboxed = True

    def __init__(
        self,
        workspace_root: str,
        agent_key: str,
        *,
        host: str | None = None,
        port: int | None = None,
        workdir: str | None = None,
    ) -> None:
        self.workspace_root = workspace_root  # 未用于执行，仅协议对齐
        self.agent_key = agent_key
        try:
            from backend.core.config import settings

            self.host = host or str(getattr(settings, "agent_computer_ssh_host", "") or "")
            self.port = port or int(getattr(settings, "agent_computer_ssh_port", 22) or 22)
            self.workdir = workdir or str(
                getattr(settings, "agent_computer_ssh_workdir", "") or "~/tevarn-ws"
            )
        except Exception:
            self.host = host or ""
            self.port = port or 22
            self.workdir = workdir or "~/tevarn-ws"
        self._dir_ready = False

    def _remote_dir(self) -> str:
        return f"{self.workdir}/{sanitize_agent_key_for_path(self.agent_key)}"

    def _ssh_argv(self, remote_cmd: str, timeout: int) -> list[str]:
        return [
            "ssh",
            "-o", "BatchMode=yes",              # 绝不交互挂死
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", f"ConnectTimeout={min(timeout, 15)}",
            "-p", str(self.port),
            self.host,
            remote_cmd,
        ]

    async def _exec(self, remote_cmd: str, timeout: int) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            *self._ssh_argv(remote_cmd, timeout),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=float(timeout))
        except asyncio.TimeoutError:
            proc.kill()
            return 124, "", f"command exceeded {timeout}s"
        from backend.computer.text_decode import decode_process_bytes

        return (
            proc.returncode or 0,
            decode_process_bytes(out),
            decode_process_bytes(err),
        )

    async def run(
        self,
        command: str,
        *,
        cwd: str,
        timeout: int = 120,
        max_output: int = 50000,
    ) -> ExecResult:
        t0 = time.monotonic()

        def _fail(msg: str, code: int = 2, error: str | None = None) -> ExecResult:
            return ExecResult(
                stdout="", stderr=msg, exit_code=code,
                duration_ms=(time.monotonic() - t0) * 1000,
                backend=self.backend_id, sandboxed=True,
                error=error or msg,
            )

        if not ssh_available():
            return _fail("ssh 客户端未安装或不在 PATH", 127, "ssh_missing")
        if not self.host:
            return _fail(
                "agent_computer_ssh_host 未配置（格式 user@host）", 2, "ssh_not_configured"
            )

        rdir = self._remote_dir()
        if not self._dir_ready:
            code, _, err = await self._exec(f"mkdir -p {shlex.quote(rdir)}", min(timeout, 30))
            if code != 0:
                return _fail(f"远端目录创建失败: {err.strip()[:300]}", code, "remote_mkdir_failed")
            self._dir_ready = True

        # cwd 语义：本地 cwd 不适用远端；命令统一在远端 agent 目录执行
        remote = f"cd {shlex.quote(rdir)} && ({command})"
        code, out, err = await self._exec(remote, timeout)
        if code == 124:
            return ExecResult(
                stdout="", stderr=f"command exceeded {timeout}s and was terminated",
                exit_code=124, duration_ms=(time.monotonic() - t0) * 1000,
                backend=self.backend_id, sandboxed=True, error="timeout",
            )
        if code == 255 and not out:
            # ssh 连接层错误（认证/网络）与命令错误区分开
            return _fail(f"ssh 连接失败: {err.strip()[:300]}", 255, "ssh_connect_failed")
        return ExecResult(
            stdout=out[:max_output].strip(),
            stderr=err[: max_output // 5].strip(),
            exit_code=code,
            duration_ms=(time.monotonic() - t0) * 1000,
            backend=self.backend_id,
            sandboxed=True,
        )
