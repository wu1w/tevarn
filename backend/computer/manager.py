"""ComputerManager：per-agent computer 管理（Phase 0.5.3）

- 按 agent_key 缓存 computer（"main"=主 Agent；子代理各自 key，互不干扰）
- 统一 execute() 入口：执行 + computer.exec 事件（start/end）+ 格式化输出
- 后端选择：settings.agent_computer_backend（bwrap|local）；
  bwrap 二进制缺失时给清晰错误，**不静默降级**（用户开了沙箱就是要隔离）
"""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from typing import Any

from backend.computer.protocol import ExecResult, ExecutionBackend

logger = logging.getLogger(__name__)


@dataclass
class AgentComputer:
    agent_key: str
    agent_label: str
    backend: ExecutionBackend

    @property
    def sandboxed(self) -> bool:
        return bool(getattr(self.backend, "sandboxed", False))


class ComputerManager:
    def __init__(self) -> None:
        self._computers: dict[str, AgentComputer] = {}

    # ─────────── computer 获取 ───────────

    def _workspace_root(self) -> str:
        try:
            from backend.tools.permissions import resolve_agent_workspace_root

            return str(resolve_agent_workspace_root())
        except Exception:
            import os

            return os.getcwd()

    def _settings(self):
        from backend.core.config import settings

        return settings

    def _make_backend(self, agent_key: str) -> ExecutionBackend:
        s = self._settings()
        backend_name = str(getattr(s, "agent_computer_backend", "auto") or "auto").lower()
        network = bool(getattr(s, "agent_computer_network", False))
        # P2-10: sandbox profile adjusts backend/network
        try:
            from backend.computer.profiles import apply_profile_to_backend_choice

            backend_name, network = apply_profile_to_backend_choice(backend_name, network)
        except Exception:
            pass
        ws = self._workspace_root()

        # auto：按平台能力自动分派（detect 是唯一事实源）
        if backend_name == "auto":
            from backend.computer.detect import detect_sandbox_capability

            backend_name = detect_sandbox_capability().mode
            if backend_name == "none":
                raise RuntimeError(
                    "当前平台无可用沙箱方案。Linux 安装 bubblewrap；"
                    "macOS 需 sandbox-exec（系统自带）；Windows 安装 WSL2 或使用受限模式。"
                    "也可设 agent_computer_backend=local 放弃沙箱。"
                )

        if backend_name == "bwrap":
            if shutil.which("bwrap") is None:
                raise RuntimeError(
                    "bwrap 未安装。请安装 bubblewrap，或设 agent_computer_backend=local"
                )
            from backend.computer.bwrap_backend import BwrapBackend

            return BwrapBackend(ws, agent_key, network=network)

        if backend_name == "seatbelt":
            from backend.computer.seatbelt_backend import (
                SeatbeltBackend,
                find_sandbox_exec,
            )

            if find_sandbox_exec() is None:
                raise RuntimeError(
                    "sandbox-exec 不可用（非 macOS 或系统组件缺失），"
                    "或设 agent_computer_backend=local"
                )
            return SeatbeltBackend(ws, agent_key, network=network)

        if backend_name in ("wsl", "wsl-bwrap"):
            from backend.computer.wsl_backend import WslBwrapBackend, wsl_has_bwrap

            if not wsl_has_bwrap():
                raise RuntimeError(
                    "WSL2 不可用或其内未安装 bubblewrap（wsl -e bash -lc 'sudo apt install bubblewrap'），"
                    "或设 agent_computer_backend=job（受限模式）/ local"
                )
            return WslBwrapBackend(ws, agent_key, network=network)

        if backend_name == "job":
            from backend.computer.job_backend import JobBackend

            return JobBackend(ws, agent_key)

        # P2a（2026-07-29）：远程/容器执行后端 —— agent 可以「活在」容器或 VPS 里
        if backend_name == "docker":
            from backend.computer.docker_backend import DockerBackend, docker_available

            if not docker_available():
                raise RuntimeError(
                    "docker 未安装或不在 PATH，或设 agent_computer_backend=local"
                )
            return DockerBackend(ws, agent_key, network=network)

        if backend_name == "ssh":
            from backend.computer.ssh_backend import SshBackend, ssh_available

            if not ssh_available():
                raise RuntimeError("ssh 客户端不可用，或设 agent_computer_backend=local")
            if not str(getattr(s, "agent_computer_ssh_host", "") or ""):
                raise RuntimeError(
                    "agent_computer_ssh_host 未配置（格式 user@host，需免密 key 认证）"
                )
            return SshBackend(ws, agent_key)

        from backend.computer.local_backend import LocalBackend

        return LocalBackend(ws)

    def get_computer(self, agent_key: str = "main", agent_label: str = "") -> AgentComputer:
        """获取（或创建）某 agent 的 computer；label 仅用于展示，后到的非空 label 覆盖"""
        key = agent_key or "main"
        comp = self._computers.get(key)
        if comp is None:
            comp = AgentComputer(
                agent_key=key,
                agent_label=agent_label or key,
                backend=self._make_backend(key),
            )
            self._computers[key] = comp
            logger.info(
                "agent computer created: key=%s backend=%s sandboxed=%s",
                key,
                comp.backend.backend_id,
                comp.sandboxed,
            )
        elif agent_label and comp.agent_label != agent_label:
            comp.agent_label = agent_label
        return comp

    def list_computers(self) -> list[AgentComputer]:
        return list(self._computers.values())

    # ─────────── 执行 + 事件 ───────────

    async def _publish(self, payload: dict[str, Any]) -> None:
        try:
            from backend.core.event_bus import event_bus

            await event_bus.publish("computer.exec", payload)
        except Exception as e:
            logger.debug("computer.exec publish failed: %s", e)

    async def execute(
        self,
        command: str,
        *,
        agent_key: str = "main",
        agent_label: str = "",
        session_id: Any = None,
        recorder: Any = None,
        cwd: str | None = None,
        timeout: int = 120,
        max_output: int = 50000,
    ) -> str:
        """经 agent 的 computer 执行命令，返回与现有 executor 相同形态的格式化字符串"""
        comp = self.get_computer(agent_key, agent_label)
        ws = self._workspace_root()
        cwd_eff = cwd or ws

        base = {
            "session_id": str(session_id) if session_id else None,
            "run_id": str(recorder.run_id) if recorder is not None and getattr(recorder, "run_id", None) else None,
            "agent_key": comp.agent_key,
            "agent_label": comp.agent_label,
        }
        await self._publish({**base, "phase": "start", "command": command[:2000]})

        result = await comp.backend.run(
            command, cwd=cwd_eff, timeout=timeout, max_output=max_output
        )

        await self._publish({
            **base,
            "phase": "end",
            "command": command[:200],
            "exit_code": result.exit_code,
            "stdout_tail": result.stdout[-2000:],
            "stderr_tail": result.stderr[-1000:],
            "duration_ms": round(result.duration_ms, 1),
            "backend": result.backend,
            "sandboxed": result.sandboxed,
        })

        return self._format(result, cwd_eff)

    @staticmethod
    def _format(result: ExecResult, cwd: str) -> str:
        """与 execute_command 既有输出形态对齐"""
        if result.error and not result.stdout and result.exit_code in (2, 127) and result.stderr:
            # 启动级错误：cwd 越界 / bwrap 缺失等，清晰报错
            return f"[Error] {result.stderr}"
        tag = f" sandbox={result.backend}" if result.sandboxed else ""
        header = f"[Exit {result.exit_code} cwd={cwd}{tag}]"
        out, err = result.stdout, result.stderr
        if result.error == "timeout":
            return f"[Timeout] {err}"
        if err:
            return f"{header}\nstdout:\n{out or '(empty)'}\n\nstderr:\n{err}"
        return out or f"{header}\n[No output]"


_manager: ComputerManager | None = None


def get_computer_manager() -> ComputerManager:
    global _manager
    if _manager is None:
        _manager = ComputerManager()
    return _manager
