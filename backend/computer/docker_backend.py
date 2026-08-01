"""docker 执行后端（P2a，2026-07-29 夜间路线图）

agent 命令在容器内执行，宿主只暴露 workspace 绑定挂载。
对标 Hermes 的 Docker terminal backend：agent 可以「活在」一个可丢弃环境里。

设计：
- 每个 agent_key 一个长驻容器（labels 标记，宕后自动重建）——
  与 dispatcher worker 池同思路：环境复用，工单隔离靠 cwd
- 镜像可配（agent_computer_docker_image，默认 python:3.12-slim）
- 网络可配（agent_computer_network，默认断网 --network none）
- cwd 越界检查与 bwrap 后端同一红线：必须在 workspace 内
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time

from backend.computer.protocol import ExecResult

logger = logging.getLogger(__name__)

_CONTAINER_PREFIX = "takton-agent-"
_GUEST_WS = "/workspace"


def docker_available() -> bool:
    return shutil.which("docker") is not None


def _container_name(agent_key: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in agent_key)[:40]
    return f"{_CONTAINER_PREFIX}{safe or 'main'}"


class DockerBackend:
    backend_id = "docker"
    sandboxed = True

    def __init__(
        self,
        workspace_root: str,
        agent_key: str,
        *,
        network: bool = False,
        image: str | None = None,
    ) -> None:
        self.workspace_root = workspace_root
        self.agent_key = agent_key
        self.network = network
        if image is None:
            try:
                from backend.core.config import settings

                image = str(
                    getattr(settings, "agent_computer_docker_image", "") or "python:3.12-slim"
                )
            except Exception:
                image = "python:3.12-slim"
        self.image = image

    async def _docker(self, *args: str, timeout: float = 60) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            "docker", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return 124, "", f"docker command exceeded {timeout}s"
        from backend.computer.text_decode import decode_process_bytes

        return (
            proc.returncode or 0,
            decode_process_bytes(out),
            decode_process_bytes(err),
        )

    async def _ensure_container(self) -> str | None:
        """确保长驻容器在跑；返回错误消息（None=OK）。"""
        name = _container_name(self.agent_key)
        code, out, _ = await self._docker(
            "ps", "-q", "--filter", f"name=^{name}$", timeout=15
        )
        if code == 0 and out.strip():
            return None
        # 清掉同名死容器再拉新
        await self._docker("rm", "-f", name, timeout=20)
        net = [] if self.network else ["--network", "none"]
        code, _, err = await self._docker(
            "run", "-d", "--name", name,
            "--label", "app=takton",
            "-v", f"{self.workspace_root}:{_GUEST_WS}",
            "-w", _GUEST_WS,
            *net,
            self.image, "sleep", "infinity",
            timeout=120,
        )
        if code != 0:
            return f"docker run failed: {err.strip()[:400]}"
        return None

    def _guest_cwd(self, cwd: str) -> str | None:
        """宿主 cwd → 容器路径；越界返回 None。"""
        try:
            ws = os.path.realpath(self.workspace_root)
            target = os.path.realpath(cwd or ws)
            if os.path.commonpath([ws, target]) != ws:
                return None
            rel = os.path.relpath(target, ws)
        except Exception:
            return None
        rel_s = rel.replace("\\", "/")
        if rel_s in (".", ""):
            return _GUEST_WS
        return f"{_GUEST_WS}/{rel_s}"

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

        if not docker_available():
            return _fail("docker 未安装或不在 PATH", 127, "docker_missing")

        guest_cwd = self._guest_cwd(cwd or self.workspace_root)
        if guest_cwd is None:
            return _fail(f"cwd 越界（必须在 workspace 内）: {cwd}")

        err = await self._ensure_container()
        if err:
            return _fail(err, 125, "container_start_failed")

        name = _container_name(self.agent_key)
        code, out, errout = await self._docker(
            "exec", "-w", guest_cwd, name, "sh", "-lc", command,
            timeout=float(timeout),
        )
        if code == 124:
            return ExecResult(
                stdout="", stderr=f"command exceeded {timeout}s and was terminated",
                exit_code=124, duration_ms=(time.monotonic() - t0) * 1000,
                backend=self.backend_id, sandboxed=True, error="timeout",
            )
        return ExecResult(
            stdout=out[:max_output].strip(),
            stderr=errout[: max_output // 5].strip(),
            exit_code=code,
            duration_ms=(time.monotonic() - t0) * 1000,
            backend=self.backend_id,
            sandboxed=True,
        )

    async def dispose(self) -> None:
        """显式回收容器（evict_worker 时调用；不调则容器复用）。"""
        await self._docker("rm", "-f", _container_name(self.agent_key), timeout=30)
