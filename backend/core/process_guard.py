"""Process-level crash forensics + light self-protection.

Silent Windows deaths (no Python traceback, empty faulthandler) leave almost
no evidence. This module writes a small **breadcrumb** file that survives
TerminateProcess / OOM kills better than fully-buffered log handlers, and
optionally samples RSS so multi-agent pressure is visible before death.

Design constraints (keep thin):
- no agent-loop coupling
- best-effort only; never raise into callers
- breadcrumb path is small append-only JSONL
"""
from __future__ import annotations

import atexit
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_installed = False
_start_mono = time.monotonic()
_last_rss_log = 0.0


def breadcrumb_path() -> Path:
    """Prefer ~/.tevarn/logs (user-writable); fall back next to package."""
    home = os.environ.get("TEVARN_HOME") or str(Path.home() / ".tevarn")
    p = Path(home) / "logs" / "process_breadcrumb.jsonl"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    except Exception:
        alt = Path(__file__).resolve().parents[2] / "logs" / "process_breadcrumb.jsonl"
        try:
            alt.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return alt


def write_breadcrumb(event: str, **extra: Any) -> None:
    """Append one JSON line and flush immediately (forensic-friendly)."""
    rec: dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        "event": event,
        "pid": os.getpid(),
        "uptime_s": round(time.monotonic() - _start_mono, 1),
        "platform": sys.platform,
    }
    if extra:
        for k, v in extra.items():
            try:
                json.dumps(v)
                rec[k] = v
            except Exception:
                rec[k] = repr(v)[:200]
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    with _lock:
        try:
            path = breadcrumb_path()
            with open(path, "a", encoding="utf-8", buffering=1) as f:
                f.write(line)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except Exception:
                    pass
        except Exception:
            pass


def sample_rss_mb() -> float | None:
    """Best-effort RSS in MiB (Windows + Unix)."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
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
                    ("PrivateUsage", ctypes.c_size_t),
                ]

            psapi = ctypes.WinDLL("psapi")
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            GetProcessMemoryInfo = psapi.GetProcessMemoryInfo
            GetProcessMemoryInfo.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(PROCESS_MEMORY_COUNTERS_EX),
                wintypes.DWORD,
            ]
            GetProcessMemoryInfo.restype = wintypes.BOOL
            counters = PROCESS_MEMORY_COUNTERS_EX()
            counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS_EX)
            handle = kernel32.GetCurrentProcess()
            if GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                return round(int(counters.WorkingSetSize) / (1024 * 1024), 1)
        else:
            # Linux: VmRSS from /proc/self/status
            status = Path("/proc/self/status").read_text(encoding="utf-8", errors="replace")
            for line in status.splitlines():
                if line.startswith("VmRSS:"):
                    kb = int(line.split()[1])
                    return round(kb / 1024, 1)
    except Exception:
        pass
    # Last resort: resource module (Unix) / ignore
    try:
        import resource  # type: ignore

        ru = resource.getrusage(resource.RUSAGE_SELF)
        # macOS: ru_maxrss bytes; Linux: kilobytes
        val = float(ru.ru_maxrss)
        if sys.platform == "darwin":
            return round(val / (1024 * 1024), 1)
        return round(val / 1024, 1)
    except Exception:
        return None


def maybe_log_rss(*, min_interval_s: float = 30.0, warn_mb: float = 1800.0) -> float | None:
    """Log RSS periodically; warn when high. Returns current RSS or None."""
    global _last_rss_log
    now = time.monotonic()
    if now - _last_rss_log < min_interval_s:
        return None
    rss = sample_rss_mb()
    if rss is None:
        return None
    _last_rss_log = now
    if rss >= warn_mb:
        logger.warning("process_guard high_rss_mb=%.1f warn_mb=%.1f", rss, warn_mb)
        write_breadcrumb("high_rss", rss_mb=rss, warn_mb=warn_mb)
    else:
        logger.info("process_guard rss_mb=%.1f", rss)
    return rss


def memory_pressure() -> str:
    """coarse: ok | elevated | critical based on RSS (+ free phys on Windows)."""
    rss = sample_rss_mb()
    if rss is None:
        return "ok"
    if rss >= 2800:
        return "critical"
    if rss >= 1800:
        return "elevated"
    # Optional: free physical memory on Windows
    try:
        if sys.platform == "win32":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):  # type: ignore[attr-defined]
                free_gb = stat.ullAvailPhys / (1024**3)
                if free_gb < 1.0 or stat.dwMemoryLoad >= 92:
                    return "critical"
                if free_gb < 2.5 or stat.dwMemoryLoad >= 85:
                    return "elevated" if rss >= 900 else "ok"
    except Exception:
        pass
    return "ok"


def _on_exit() -> None:
    write_breadcrumb("atexit", rss_mb=sample_rss_mb())


def install_process_guard(*, role: str = "backend") -> None:
    """Idempotent: atexit + startup breadcrumb + optional faulthandler re-assert."""
    global _installed
    if _installed:
        return
    _installed = True
    write_breadcrumb(
        "guard_install",
        role=role,
        python=sys.version.split()[0],
        executable=sys.executable,
        rss_mb=sample_rss_mb(),
    )
    try:
        atexit.register(_on_exit)
    except Exception:
        pass
    # Re-assert Selector policy (belt if something reset it)
    if sys.platform == "win32":
        try:
            import asyncio

            pol = asyncio.get_event_loop_policy()
            name = type(pol).__name__
            if "Proactor" in name:
                asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
                write_breadcrumb("policy_switched_from_proactor")
                logger.warning(
                    "process_guard: switched event loop policy from Proactor → Selector"
                )
            else:
                write_breadcrumb("policy_ok", policy=name)
        except Exception as e:
            write_breadcrumb("policy_check_fail", err=str(e)[:160])
