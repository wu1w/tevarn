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

    def _isolation_policy(
        self, process_id: str | None, is_workforce: bool
    ) -> dict[str, Any]:
        """P0 gap-fill：向 Rust isolation supervisor 解析执行策略。"""
        if not process_id:
            return {}
        try:
            from backend.kernel import get_kernel

            k = get_kernel()
            if hasattr(k, "isolation_resolve"):
                return k.isolation_resolve(
                    process_id, is_workforce=is_workforce
                ) or {}
            if hasattr(k, "_call"):
                return (
                    k._call(
                        "isolation_resolve",
                        {
                            "process_id": process_id,
                            "is_workforce": is_workforce,
                        },
                    )
                    or {}
                )
        except Exception as e:
            logger.debug("isolation_resolve skip: %s", e)
        return {}

    def _make_backend(
        self,
        agent_key: str,
        *,
        process_id: str | None = None,
        force_backend: str | None = None,
        force_network: bool | None = None,
    ) -> ExecutionBackend:
        s = self._settings()
        backend_name = str(
            force_backend
            or getattr(s, "agent_computer_backend", "auto")
            or "auto"
        ).lower()
        network = (
            bool(force_network)
            if force_network is not None
            else bool(getattr(s, "agent_computer_network", False))
        )
        # P2-10: sandbox profile adjusts backend/network
        try:
            from backend.computer.profiles import apply_profile_to_backend_choice

            backend_name, network = apply_profile_to_backend_choice(backend_name, network)
        except Exception:
            pass

        # P0 gap-fill: kernel isolation policy overrides
        is_wf = str(agent_key or "").startswith("wf:") or str(agent_key or "").startswith(
            "workforce"
        )
        iso = self._isolation_policy(process_id, is_wf)
        if iso:
            if iso.get("sandbox_required") and backend_name == "local":
                backend_name = "auto"
                logger.info(
                    "isolation force sandbox: profile=%s agent=%s local→auto",
                    iso.get("id"),
                    agent_key,
                )
            if iso.get("network") is False:
                network = False
            if iso.get("sandbox_required") and backend_name == "off":
                backend_name = "auto"

        ws = self._workspace_root()

        def _sandbox_required_fail(detail: str = "") -> RuntimeError:
            """workforce/untrusted 无沙箱：统一 fail-closed 文案，禁止静默 local。"""
            from backend.kernel.tool_gate import workforce_sandbox_fail_message

            if is_wf or iso.get("sandbox_required"):
                return RuntimeError(
                    workforce_sandbox_fail_message(
                        profile_id=str(iso.get("id") or ("workforce" if is_wf else "strict")),
                        detail=detail,
                    ).removeprefix("[Error] ")
                )
            return RuntimeError(detail or "sandbox unavailable")

        # auto：按平台能力自动分派（detect 是唯一事实源）
        if backend_name == "auto":
            from backend.computer.detect import detect_sandbox_capability

            backend_name = detect_sandbox_capability().mode
            if backend_name == "none":
                if iso.get("sandbox_required") or is_wf:
                    raise _sandbox_required_fail("detect.mode=none")
                raise RuntimeError(
                    "当前平台无可用沙箱方案。Linux 安装 bubblewrap；"
                    "macOS 需 sandbox-exec（系统自带）；Windows 安装 WSL2 或使用受限模式。"
                    "也可设 agent_computer_backend=local 放弃沙箱。"
                )

        # 最后防线：sandbox_required / workforce 禁止落到 local
        if (iso.get("sandbox_required") or is_wf) and backend_name == "local":
            from backend.computer.detect import detect_sandbox_capability

            cap = detect_sandbox_capability()
            if cap.mode != "none":
                backend_name = cap.mode
            else:
                raise _sandbox_required_fail("backend forced away from local")

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

            mem_lim, proc_lim = self._job_limits_from_resources(process_id)
            return JobBackend(
                ws,
                agent_key,
                memory_limit=mem_lim,
                process_limit=proc_lim,
            )

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

    def _job_limits_from_resources(
        self, process_id: str | None
    ) -> tuple[int, int]:
        """从 kernel 资源账户推导 Job Object 硬限（Windows）。

        - memory_limit ← resource memory_bytes.limit（下限 64MiB，上限 4GiB 默认顶）
        - process_limit ← resource child_proc.remaining+1 与 limit 的较小合理值
        无 process / 无账户时回落 JobBackend 默认。
        """
        from backend.computer.job_backend import (
            _DEFAULT_ACTIVE_PROCESS_LIMIT,
            _DEFAULT_MEMORY_LIMIT,
        )

        mem = _DEFAULT_MEMORY_LIMIT
        procs = _DEFAULT_ACTIVE_PROCESS_LIMIT
        if not process_id:
            return mem, procs
        try:
            from backend.kernel import get_kernel

            k = get_kernel()
            usage = (
                k.resource_usage(process_id)
                if hasattr(k, "resource_usage")
                else (k._call("resource_usage", {"process_id": process_id}) or {})
            )
            if not isinstance(usage, dict):
                return mem, procs
            mb = usage.get("memory_bytes") or {}
            if isinstance(mb, dict) and mb.get("limit") is not None:
                lim = int(mb["limit"])
                # OS Job 至少 64MiB；不超过默认 4GiB 顶（账户逻辑限额可更小）
                mem = max(64 * 1024 * 1024, min(lim, _DEFAULT_MEMORY_LIMIT))
            cp = usage.get("child_proc") or {}
            if isinstance(cp, dict):
                if cp.get("limit") is not None:
                    lim = int(cp["limit"])
                    # ActiveProcessLimit 至少 2（cmd + 子），至多默认 128
                    procs = max(2, min(lim, _DEFAULT_ACTIVE_PROCESS_LIMIT))
                rem = cp.get("remaining")
                if rem is not None:
                    # 剩余配额 + 当前将起的 1 个，再夹紧
                    procs = max(2, min(procs, int(rem) + 1, _DEFAULT_ACTIVE_PROCESS_LIMIT))
        except Exception as e:
            logger.debug("job limits from resources skip: %s", e)
        return mem, procs

    def get_computer(
        self,
        agent_key: str = "main",
        agent_label: str = "",
        *,
        process_id: str | None = None,
        rebuild: bool = False,
    ) -> AgentComputer:
        """获取（或创建）某 agent 的 computer；label 仅用于展示，后到的非空 label 覆盖"""
        key = agent_key or "main"
        # cache key includes process isolation context when present
        cache_key = f"{key}:{process_id}" if process_id else key
        if rebuild:
            self._computers.pop(cache_key, None)
            self._computers.pop(key, None)
        comp = self._computers.get(cache_key) or (
            None if process_id else self._computers.get(key)
        )
        if comp is None:
            comp = AgentComputer(
                agent_key=key,
                agent_label=agent_label or key,
                backend=self._make_backend(key, process_id=process_id),
            )
            self._computers[cache_key] = comp
            logger.info(
                "agent computer created: key=%s backend=%s sandboxed=%s process=%s",
                key,
                comp.backend.backend_id,
                comp.sandboxed,
                (process_id or "")[:8],
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
        process_id: str | None = None,
    ) -> str:
        """经 agent 的 computer 执行命令，返回与现有 executor 相同形态的格式化字符串"""
        # 允许从 recorder 取 kernel process id
        kpid = process_id or (
            str(getattr(recorder, "kernel_process_id", "") or "") or None
        )
        comp = self.get_computer(agent_key, agent_label, process_id=kpid)
        ws = self._workspace_root()
        cwd_eff = cwd or ws

        base = {
            "session_id": str(session_id) if session_id else None,
            "run_id": str(recorder.run_id) if recorder is not None and getattr(recorder, "run_id", None) else None,
            "agent_key": comp.agent_key,
            "agent_label": comp.agent_label,
        }
        await self._publish({**base, "phase": "start", "command": command[:2000]})

        # Hardening：spawn 前记录资源快照（tool_gate 已对 command 预扣 child_proc；
        # 此处若 used≥limit 则 fail-closed，防止绕过 gate 的直调）。
        if kpid:
            try:
                from backend.kernel import get_kernel

                k = get_kernel()
                usage = (
                    k.resource_usage(kpid)
                    if hasattr(k, "resource_usage")
                    else None
                )
                if isinstance(usage, dict):
                    cp = usage.get("child_proc") or {}
                    lim, used = cp.get("limit"), cp.get("used")
                    if lim is not None and used is not None and int(used) > int(lim):
                        return (
                            f"[Error] 资源配额不足——child_proc used={used}/{lim}。"
                            "请降低并发命令或提高进程资源上限。"
                        )
            except Exception as e:
                logger.debug("pre-spawn resource check skip: %s", e)

        # P0 gap-fill：向 isolation supervisor 登记 spawn（策略强制）
        iso_handle = None
        try:
            from backend.kernel import get_kernel

            k = get_kernel()
            if kpid and hasattr(k, "_call"):
                iso_handle = k._call(
                    "isolation_spawn",
                    {
                        "process_id": kpid,
                        "command": command[:500],
                        "backend": getattr(comp.backend, "backend_id", "local"),
                    },
                )
        except Exception as e:
            # isolation deny (e.g. untrusted+local) must fail closed
            msg = str(e)
            if (
                "isolation" in msg.lower()
                or "sandbox" in msg.lower()
                or "local" in msg.lower()
                or "workforce" in msg.lower()
            ):
                logger.warning("isolation_spawn denied: %s", e)
                is_wf = str(agent_key or "").startswith("wf:")
                if is_wf:
                    from backend.kernel.tool_gate import workforce_sandbox_fail_message

                    return workforce_sandbox_fail_message(
                        profile_id="workforce", detail=str(e)
                    )
                return f"[Error] isolation denied: {e}"
            logger.debug("isolation_spawn skip: %s", e)

        # 编制/强制沙箱：backend 不得是 unsandboxed local
        if not comp.sandboxed and (
            str(agent_key or "").startswith("wf:")
            or (
                kpid
                and self._isolation_policy(
                    kpid, str(agent_key or "").startswith("wf:")
                ).get("sandbox_required")
            )
        ):
            from backend.kernel.tool_gate import workforce_sandbox_fail_message

            return workforce_sandbox_fail_message(
                profile_id="workforce",
                detail=f"backend={getattr(comp.backend, 'backend_id', '?')} not sandboxed",
            )

        result = await comp.backend.run(
            command, cwd=cwd_eff, timeout=timeout, max_output=max_output
        )

        # 资源加深：RSS 上报 + Linux cgroup（可选，失败不阻断）
        if kpid:
            try:
                from backend.core.config import settings as _rs

                if bool(getattr(_rs, "agent_resource_rss_sample", True)):
                    from backend.kernel.resource_os import sample_and_report

                    sample_and_report(kpid)
                if bool(getattr(_rs, "agent_resource_cgroup_enabled", False)):
                    from backend.kernel.resource_os import cgroup_apply

                    mem_lim = None
                    try:
                        from backend.kernel import get_kernel

                        usage = get_kernel().resource_usage(kpid) or {}
                        lim = (usage.get("memory_bytes") or {}).get("limit")
                        if lim is not None:
                            mem_lim = int(lim)
                    except Exception:
                        pass
                    cgroup_apply(f"proc-{kpid[:12]}", memory_max_bytes=mem_lim)
            except Exception as _re:
                logger.debug("resource_os post-exec: %s", _re)

        if iso_handle and isinstance(iso_handle, dict) and iso_handle.get("id"):
            try:
                from backend.kernel import get_kernel

                get_kernel()._call(
                    "isolation_complete",
                    {
                        "handle_id": iso_handle["id"],
                        "exit_code": int(result.exit_code),
                    },
                )
            except Exception:
                pass

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
