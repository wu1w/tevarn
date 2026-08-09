"""Windows Job Object 受限执行后端（2026-07-26）

定位：Windows 无 bwrap/sandbox-exec 等价物时的**受限模式**（非完整文件系统沙箱）：
- Job Object 进程组管控：
  · KILL_ON_JOB_CLOSE —— 命令结束/超时/宿主退出时整个进程树被强杀（防残留/逃逸）
  · ACTIVE_PROCESS 上限 —— 防 fork 炸弹
  · PROCESS_MEMORY_LIMIT —— 单进程内存限额
- cwd 限定 workspace（文件系统边界靠权限控制台高危命令策略兜底）

诚实边界：Job Object 不提供文件系统/注册表隔离。完整隔离需 AppContainer
（需原生 helper，超出纯 Python 范围）或 WSL 沙箱（WslBwrapBackend）。

ctypes 全部延迟到执行期 import——本模块在 Linux/macOS 上可安全 import（测试需要）。
"""
from __future__ import annotations

import asyncio
import logging
import ntpath
import os
import time
from pathlib import Path

from backend.computer.protocol import ExecResult

logger = logging.getLogger(__name__)

# Job Object 常量
_JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
_JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
_JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JobObjectExtendedLimitInformation = 9

# cargo/rustc + link can peak well above 2–3GB; 4GB was tight and produced
# rustc AppCrash under multi-agent cargo test/clippy. 6GB still bounds runaway.
_DEFAULT_MEMORY_LIMIT = 6 * 1024 * 1024 * 1024  # 单进程 / Job 合计 6GB
_DEFAULT_ACTIVE_PROCESS_LIMIT = 128


def _job_structures(ctypes):
    """定义 Job Object 结构体（延迟构造，避免顶层 Windows 依赖）。"""

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", ctypes.c_uint32),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_uint32),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_uint32),
            ("SchedulingClass", ctypes.c_uint32),
        ]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    return JOBOBJECT_EXTENDED_LIMIT_INFORMATION


class _JobHandle:
    """RAII：关闭时 KILL_ON_JOB_CLOSE 强杀整个进程树。"""

    def __init__(self, memory_limit: int, process_limit: int) -> None:
        import ctypes

        self._ctypes = ctypes
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        self._kernel32 = kernel32
        self.handle = kernel32.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError("CreateJobObjectW failed")

        info_cls = _job_structures(ctypes)
        info = info_cls()
        # Hardening：进程内存 + Job 合计内存 + 活跃进程数 + 关闭时杀树
        # memory/process 限额可由 ComputerManager 从 kernel resource_usage 注入
        info.BasicLimitInformation.LimitFlags = (
            _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            | _JOB_OBJECT_LIMIT_PROCESS_MEMORY
            | _JOB_OBJECT_LIMIT_JOB_MEMORY
            | _JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        )
        info.ProcessMemoryLimit = memory_limit
        info.JobMemoryLimit = memory_limit
        info.BasicLimitInformation.ActiveProcessLimit = process_limit
        ok = kernel32.SetInformationJobObject(
            self.handle,
            _JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            kernel32.CloseHandle(self.handle)
            raise OSError("SetInformationJobObject failed")

    def assign(self, process_handle: int) -> None:
        if not self._kernel32.AssignProcessToJobObject(self.handle, process_handle):
            raise OSError("AssignProcessToJobObject failed")

    def terminate(self, exit_code: int = 1) -> None:
        """强制 TerminateJobObject：cancel/超时在 communicate 阻塞时也能杀树。"""
        if not self.handle:
            return
        try:
            # BOOL TerminateJobObject(HANDLE hJob, UINT uExitCode)
            self._kernel32.TerminateJobObject(self.handle, exit_code)
        except Exception as e:
            logger.debug("TerminateJobObject skip: %s", e)

    def close(self) -> None:
        if self.handle:
            # 先 terminate 再 close：KILL_ON_JOB_CLOSE 依赖 close，但显式 terminate
            # 在句柄泄漏/半关闭路径更可靠
            try:
                self.terminate(1)
            except Exception:
                pass
            self._kernel32.CloseHandle(self.handle)
            self.handle = None


class JobBackend:
    """Windows 受限执行后端（资源/进程管控，非完整 FS 沙箱）。"""

    backend_id = "job"
    sandboxed = True  # 提供进程组隔离（边界见模块 docstring）

    def __init__(
        self,
        workspace_root: str,
        agent_key: str = "main",
        *,
        memory_limit: int = _DEFAULT_MEMORY_LIMIT,
        process_limit: int = _DEFAULT_ACTIVE_PROCESS_LIMIT,
    ) -> None:
        # Windows 路径统一走 ntpath（代码在 Linux 上被测试时 os.path 语义是错的）
        self.workspace_root = ntpath.abspath(workspace_root)
        self.agent_key = agent_key
        self.memory_limit = memory_limit
        self.process_limit = process_limit
        from backend.computer.pathutil import sanitize_agent_key_for_path

        safe_key = sanitize_agent_key_for_path(agent_key)
        self.agent_home = ntpath.join(
            self.workspace_root, ".computers", safe_key, "home"
        )
        # Capture *host* profile before we rewrite USERPROFILE for the job.
        # Used so tools can still open real ~/.tevarn logs (self-check / ops).
        self.host_user_home = (
            os.environ.get("TEVARN_HOST_HOME")
            or os.environ.get("USERPROFILE")
            or os.environ.get("HOME")
            or str(Path.home())
        )
        self.host_tevarn_home = (
            os.environ.get("TEVARN_HOME")
            or ntpath.join(self.host_user_home, ".tevarn")
        )

    def _ensure_dirs(self) -> None:
        Path(self.agent_home).mkdir(parents=True, exist_ok=True)
        try:
            from backend.agent._tevarn_paths import ensure_sandbox_tevarn_link

            ensure_sandbox_tevarn_link(self.agent_home, self.host_tevarn_home)
        except Exception:
            pass

    def _allowed_cwd_roots(self) -> list[str]:
        """cwd 允许根：workspace + 本轮 extra + 宿主数据根（Job 无完整 FS 隔离）。"""
        roots = [self.workspace_root]
        try:
            from backend.tools.permissions import (
                get_run_extra_roots,
                get_run_workspace_root,
                host_data_roots,
            )

            run = get_run_workspace_root()
            if run:
                roots.append(str(run))
            roots.extend(get_run_extra_roots() or [])
            roots.extend(host_data_roots() or [])
        except Exception:
            pass
        # 开发仓显式 env
        for env_key in ("TEVARN_DEV_ROOT", "TEVARN_REPO_ROOT", "TEVARN_FILE_BROWSER_ROOT"):
            raw = (os.environ.get(env_key) or "").strip()
            if raw:
                roots.append(raw)
        seen: set[str] = set()
        out: list[str] = []
        for r in roots:
            try:
                ar = ntpath.abspath(str(r))
            except Exception:
                continue
            key = ar.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(ar)
        return out

    def _check_cwd(self, cwd: str) -> str | None:
        real = ntpath.abspath(cwd)
        for root in self._allowed_cwd_roots():
            if real == root or real.startswith(root + ntpath.sep):
                return None
        return (
            f"cwd 超出允许范围（workspace={self.workspace_root} 及宿主/开发数据根）: {real}。"
            "请在 workspace 内执行，或把目录配进 session workspace_root / TEVARN_DEV_ROOT。"
        )

    def _run_sync(self, command: str, cwd: str, timeout: int) -> tuple[str, str, int]:
        """同步执行（在线程中跑）：Job Object 包裹 cmd 进程。"""
        import subprocess

        # Always wrap as cmd.exe /d /c <cmd> — strip model-added outer cmd /c first.
        try:
            from backend.core.safe_subprocess import normalize_windows_job_command

            command = normalize_windows_job_command(command)
        except Exception:
            pass

        job = _JobHandle(self.memory_limit, self.process_limit)
        host_home = self.host_user_home or os.environ.get("USERPROFILE") or ""
        path = os.environ.get("PATH", "")
        # Ensure common host tool dirs (scoop/cargo/rustup) stay on PATH even if
        # Electron launched with a thin env.
        extras: list[str] = []
        if host_home:
            extras.extend(
                [
                    os.path.join(host_home, "scoop", "shims"),
                    os.path.join(host_home, "scoop", "apps", "rust", "current", "bin"),
                    os.path.join(host_home, "scoop", "persist", "rustup", ".cargo", "bin"),
                    os.path.join(host_home, "scoop", "apps", "rustup", "current", ".cargo", "bin"),
                    os.path.join(host_home, ".cargo", "bin"),
                    os.path.join(host_home, ".rustup", "toolchains"),
                ]
            )
        for p in extras:
            if p and os.path.isdir(p) and p.lower() not in path.lower():
                path = p + os.pathsep + path
        env = {
            "HOME": self.agent_home,
            # Keep host USERPROFILE so rustup/cargo resolve host toolchains
            # (sandbox profile only for HOME-style isolation of writes).
            "USERPROFILE": host_home or self.agent_home,
            # Real host paths for Tevarn data / logs (do not use sandbox USERPROFILE)
            "TEVARN_HOST_HOME": self.host_user_home,
            "TEVARN_HOME": self.host_tevarn_home,
            "PATH": path,
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", r"C:\Windows"),
            "TEMP": os.environ.get("TEMP", r"C:\Windows\Temp"),
            "TMP": os.environ.get("TMP", r"C:\Windows\Temp"),
            # Prefer UTF-8 from Python tools inside the job (stderr still may be GBK
            # from cmd.exe / CRT — decode_process_bytes handles both).
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        }
        # Preserve host identity / cargo target (RUSTUP_HOME resolved below)
        for k in (
            "CARGO_TARGET_DIR",
            "RUSTFLAGS",
            "HOMEDRIVE",
            "HOMEPATH",
            "USERNAME",
            "USERDOMAIN",
            "APPDATA",
            "LOCALAPPDATA",
            "CARGO_HOME",
            "RUSTUP_HOME",
        ):
            if os.environ.get(k):
                env[k] = os.environ[k]

        # Healthy rust + MSVC: fix broken RUSTUP_HOME (missing msvc rustc) which
        # caused cargo "Missing manifest" → agent where/dir/rustup 复读.
        try:
            from backend.core.msvc_env import (
                apply_host_rust_env,
                merge_msvc_env,
                needs_msvc_toolchain,
                prepend_vcvars_call,
                rewrite_cargo_to_absolute,
            )

            env = apply_host_rust_env(env, host_home)
            env = merge_msvc_env(env)
            if needs_msvc_toolchain(command):
                command = rewrite_cargo_to_absolute(command, host_home)
                command = prepend_vcvars_call(command)
                logger.info(
                    "job_backend: rust+MSVC ready RUSTUP_HOME=%s CARGO=%s",
                    env.get("RUSTUP_HOME", "(unset→scoop)"),
                    env.get("CARGO", "?"),
                )
        except Exception as _msvc_e:
            logger.debug("job_backend rust/msvc merge skip: %s", _msvc_e)

        try:
            from backend.computer.text_decode import decode_process_bytes

            proc = subprocess.Popen(
                ["cmd.exe", "/d", "/c", command],
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            job.assign(proc._handle)  # type: ignore[attr-defined]
            try:
                out_b, err_b = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                # 先 TerminateJobObject 再 close，避免仅依赖 KILL_ON_JOB_CLOSE
                try:
                    job.terminate(124)
                except Exception:
                    pass
                job.close()
                try:
                    proc.kill()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass
                raise
            except Exception:
                # cancel / 其它异常：强制杀树，避免 read 阻塞拖死
                try:
                    job.terminate(1)
                except Exception:
                    pass
                try:
                    proc.kill()
                except Exception:
                    pass
                raise
            return (
                decode_process_bytes(out_b),
                decode_process_bytes(err_b),
                proc.returncode or 0,
            )
        finally:
            try:
                job.terminate(1)
            except Exception:
                pass
            job.close()

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

        if os.name != "nt":
            return _err("JobBackend 仅支持 Windows（请改用 local 后端）", 127)

        cwd_err = self._check_cwd(cwd)
        if cwd_err:
            return _err(cwd_err, code=2)

        try:
            self._ensure_dirs()
        except Exception as e:
            return _err(f"沙箱 HOME 创建失败: {e}")

        try:
            out, err, rc = await asyncio.to_thread(
                self._run_sync, command, os.path.abspath(cwd), timeout
            )
            if len(out) > max_output:
                out = out[:max_output] + "\n...[stdout truncated]"
            if len(err) > max_output // 2:
                err = err[: max_output // 2] + "\n...[stderr truncated]"
            return ExecResult(
                stdout=out.strip(),
                stderr=err.strip(),
                exit_code=rc,
                duration_ms=(time.monotonic() - t0) * 1000,
                backend=self.backend_id,
                sandboxed=True,
            )
        except Exception as e:
            name = type(e).__name__
            if name == "TimeoutExpired":
                return _err(f"command exceeded {timeout}s and was terminated", 124)
            return _err(f"受限执行异常: {e}")
