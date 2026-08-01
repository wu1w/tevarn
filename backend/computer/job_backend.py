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

_DEFAULT_MEMORY_LIMIT = 4 * 1024 * 1024 * 1024  # 单进程 / Job 合计 4GB
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

    def close(self) -> None:
        if self.handle:
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
        # Used so tools can still open real ~/.takton logs (self-check / ops).
        self.host_user_home = (
            os.environ.get("TAKTON_HOST_HOME")
            or os.environ.get("USERPROFILE")
            or os.environ.get("HOME")
            or str(Path.home())
        )
        self.host_takton_home = (
            os.environ.get("TAKTON_HOME")
            or ntpath.join(self.host_user_home, ".takton")
        )

    def _ensure_dirs(self) -> None:
        Path(self.agent_home).mkdir(parents=True, exist_ok=True)
        try:
            from backend.agent._takton_paths import ensure_sandbox_takton_link

            ensure_sandbox_takton_link(self.agent_home, self.host_takton_home)
        except Exception:
            pass

    def _check_cwd(self, cwd: str) -> str | None:
        real = ntpath.abspath(cwd)
        root = self.workspace_root
        if real == root or real.startswith(root + ntpath.sep):
            return None
        return f"cwd 超出沙箱 workspace（{root}）: {real}"

    def _run_sync(self, command: str, cwd: str, timeout: int) -> tuple[str, str, int]:
        """同步执行（在线程中跑）：Job Object 包裹 cmd 进程。"""
        import subprocess

        job = _JobHandle(self.memory_limit, self.process_limit)
        env = {
            "HOME": self.agent_home,
            "USERPROFILE": self.agent_home,
            # Real host paths for Takton data / logs (do not use sandbox USERPROFILE)
            "TAKTON_HOST_HOME": self.host_user_home,
            "TAKTON_HOME": self.host_takton_home,
            "PATH": os.environ.get("PATH", ""),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", r"C:\Windows"),
            "TEMP": os.environ.get("TEMP", r"C:\Windows\Temp"),
            "TMP": os.environ.get("TMP", r"C:\Windows\Temp"),
            # Prefer UTF-8 from Python tools inside the job (stderr still may be GBK
            # from cmd.exe / CRT — decode_process_bytes handles both).
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        }
        # Preserve HOMEDRIVE/HOMEPATH so host_home() can recover real profile
        for k in ("HOMEDRIVE", "HOMEPATH", "USERNAME", "USERDOMAIN"):
            if os.environ.get(k):
                env[k] = os.environ[k]
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
                job.close()  # KILL_ON_JOB_CLOSE 强杀进程树
                proc.wait()
                raise
            return (
                decode_process_bytes(out_b),
                decode_process_bytes(err_b),
                proc.returncode or 0,
            )
        finally:
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
