"""OS 侧资源加深：RSS 采样 + Linux cgroup v2 可选硬限。

与 Rust ``memory_bytes`` 逻辑账户对齐：
- ``sample_and_report(process_id)`` 读本机相关进程 RSS，上报 kernel
- ``cgroup_apply`` 在 Linux 上尝试为子进程组设 memory.max（失败不阻断）

不杀无关系统进程；cgroup 仅写用户可写的 cgroup 路径。
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _settings():
    try:
        from backend.core.config import settings

        return settings
    except Exception:
        return None


def cgroup_enabled() -> bool:
    s = _settings()
    if s is None:
        return bool(os.environ.get("TAKTON_CGROUP_ENABLED", "").lower() in ("1", "true"))
    return bool(getattr(s, "agent_resource_cgroup_enabled", False))


def sample_rss_bytes_self() -> int | None:
    """当前 Python 进程 RSS（字节）。"""
    try:
        import resource  # Unix

        # ru_maxrss: Linux KB, macOS bytes
        usage = resource.getrusage(resource.RUSAGE_SELF)
        val = int(usage.ru_maxrss)
        if sys.platform == "darwin":
            return val
        return val * 1024
    except Exception:
        pass
    # Windows / fallback via ctypes or psutil
    try:
        import psutil  # type: ignore

        return int(psutil.Process(os.getpid()).memory_info().rss)
    except Exception:
        pass
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            # Win7+：优先 kernel32；旧系统回落 psapi
            gpm = getattr(kernel32, "K32GetProcessMemoryInfo", None)
            if gpm is None:
                psapi = ctypes.WinDLL("psapi", use_last_error=True)
                gpm = psapi.GetProcessMemoryInfo
            gpm.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), wintypes.DWORD]
            gpm.restype = wintypes.BOOL
            counters = PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
            h = kernel32.GetCurrentProcess()
            if gpm(h, ctypes.byref(counters), counters.cb):
                return int(counters.WorkingSetSize)
        except Exception as e:
            logger.debug("win rss sample: %s", e)
    return None


def sample_rss_bytes_pid(pid: int) -> int | None:
    if pid <= 0:
        return None
    try:
        import psutil  # type: ignore

        return int(psutil.Process(pid).memory_info().rss)
    except Exception:
        pass
    if sys.platform.startswith("linux"):
        try:
            status = Path(f"/proc/{pid}/status").read_text(encoding="utf-8", errors="replace")
            for line in status.splitlines():
                if line.startswith("VmRSS:"):
                    # kB
                    parts = line.split()
                    return int(parts[1]) * 1024
        except Exception:
            pass
    return None


def report_rss_to_kernel(process_id: str, rss_bytes: int) -> dict[str, Any]:
    """将 RSS 同步为 memory_bytes 使用量（取 max），超限则返回 over_limit。"""
    out: dict[str, Any] = {
        "process_id": process_id,
        "rss_bytes": rss_bytes,
        "ok": False,
    }
    try:
        from backend.kernel import get_kernel

        k = get_kernel()
        if hasattr(k, "_call"):
            r = k._call(
                "resource_report_rss",
                {"process_id": process_id, "rss_bytes": int(rss_bytes)},
            )
            if isinstance(r, dict):
                out.update(r)
                out["ok"] = True
                return out
        # fallback: charge delta against usage
        usage = {}
        if hasattr(k, "resource_usage"):
            usage = k.resource_usage(process_id) or {}
        elif hasattr(k, "_call"):
            usage = k._call("resource_usage", {"process_id": process_id}) or {}
        mb = usage.get("memory_bytes") or {}
        used = int(mb.get("used") or 0)
        limit = mb.get("limit")
        if rss_bytes > used and hasattr(k, "resource_charge"):
            try:
                k.resource_charge(process_id, "memory_bytes", rss_bytes - used)
            except Exception as e:
                out["over_limit"] = True
                out["error"] = str(e)
                return out
        out["used"] = max(used, rss_bytes)
        out["limit"] = limit
        out["over_limit"] = (
            limit is not None and out["used"] > int(limit)
        )
        out["ok"] = True
    except Exception as e:
        out["error"] = str(e)
    return out


def sample_and_report(process_id: str, *, os_pid: int | None = None) -> dict[str, Any]:
    rss = sample_rss_bytes_pid(os_pid) if os_pid else sample_rss_bytes_self()
    if rss is None:
        return {"ok": False, "error": "rss sample unavailable", "process_id": process_id}
    return report_rss_to_kernel(process_id, rss)


def cgroup_root() -> Path | None:
    """用户可写 cgroup v2 根（若存在）。"""
    candidates = [
        Path("/sys/fs/cgroup"),
        Path(os.environ.get("TAKTON_CGROUP_ROOT", "") or ""),
    ]
    for p in candidates:
        if not p or str(p) == ".":
            continue
        if (p / "cgroup.controllers").is_file() or (p / "memory.max").exists() or p.is_dir():
            # controllers file is the v2 marker at root
            if (p / "cgroup.controllers").is_file() or any(p.glob("*/memory.max")):
                return p
    return None


def cgroup_apply(
    group_name: str,
    *,
    memory_max_bytes: int | None = None,
    pids: list[int] | None = None,
) -> dict[str, Any]:
    """创建/更新 cgroup 并可选迁入 pid。失败返回 ok=False，不抛。"""
    if not cgroup_enabled():
        return {"ok": False, "skipped": True, "reason": "cgroup disabled"}
    if not sys.platform.startswith("linux"):
        return {"ok": False, "skipped": True, "reason": "not linux"}
    root = cgroup_root()
    if root is None:
        return {"ok": False, "skipped": True, "reason": "no cgroup v2"}
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in group_name)[:64]
    path = root / "takton" / safe
    try:
        path.mkdir(parents=True, exist_ok=True)
        # enable memory controller in parent if needed
        parent = path.parent
        subtree = parent / "cgroup.subtree_control"
        if subtree.is_file():
            try:
                cur = subtree.read_text(encoding="utf-8")
                if "memory" not in cur:
                    subtree.write_text("+memory\n", encoding="utf-8")
            except OSError as e:
                logger.debug("subtree_control: %s", e)
        if memory_max_bytes is not None and memory_max_bytes > 0:
            (path / "memory.max").write_text(str(int(memory_max_bytes)), encoding="utf-8")
        moved = []
        for pid in pids or []:
            try:
                (path / "cgroup.procs").write_text(str(int(pid)), encoding="utf-8")
                moved.append(pid)
            except OSError as e:
                logger.debug("cgroup move pid=%s: %s", pid, e)
        return {
            "ok": True,
            "path": str(path),
            "memory_max": memory_max_bytes,
            "pids": moved,
        }
    except OSError as e:
        return {"ok": False, "error": str(e)}


def status() -> dict[str, Any]:
    return {
        "cgroup_enabled": cgroup_enabled(),
        "cgroup_root": str(cgroup_root()) if cgroup_root() else None,
        "platform": sys.platform,
        "self_rss": sample_rss_bytes_self(),
    }
