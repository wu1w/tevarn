#!/usr/bin/env python3
"""Phase 5.1c：采样当前进程 / 可选 backend 进程 RSS，写入报告。

用法:
  .venv/Scripts/python.exe scripts/measure_rss.py
  .venv/Scripts/python.exe scripts/measure_rss.py --pid 12345
  .venv/Scripts/python.exe scripts/measure_rss.py --label idle-backend

不强制 psutil：Windows 用 ctypes / 失败时写入手工表模板。
"""
from __future__ import annotations

import argparse
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "PHASE5_RESOURCE_BASELINE.md"


def _rss_bytes(pid: int) -> int | None:
    try:
        import psutil  # type: ignore

        return int(psutil.Process(pid).memory_info().rss)
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
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            PROCESS_QUERY_INFORMATION = 0x0400
            PROCESS_VM_READ = 0x0010
            handle = kernel32.OpenProcess(
                PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid
            )
            if not handle:
                return None
            try:
                counters = PROCESS_MEMORY_COUNTERS()
                counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
                if psapi.GetProcessMemoryInfo(
                    handle, ctypes.byref(counters), counters.cb
                ):
                    return int(counters.WorkingSetSize)
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return None
    try:
        # Linux: /proc/self/status VmRSS
        status = Path(f"/proc/{pid}/status")
        if status.is_file():
            for line in status.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("VmRSS:"):
                    kb = int(line.split()[1])
                    return kb * 1024
    except Exception:
        pass
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, default=os.getpid())
    ap.add_argument("--label", default="sample")
    ap.add_argument("--append", action="store_true", default=True)
    args = ap.parse_args()

    rss = _rss_bytes(args.pid)
    mb = (rss / (1024 * 1024)) if rss is not None else None
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    ver = "unknown"
    try:
        ver = (ROOT / "backend" / "VERSION").read_text(encoding="utf-8").strip()
    except Exception:
        pass

    line = (
        f"| {now} | {ver} | {args.label} | {args.pid} | "
        f"{mb:.1f} MB |"
        if mb is not None
        else f"| {now} | {ver} | {args.label} | {args.pid} | N/A (install psutil or run on Win/Linux) |"
    )

    header = """# Phase 5.1c 资源基线

目标（DEV_PLAN）：
- 空载（backend idle）RSS **&lt; 500 MB**
- 单会话轻聊峰值 **&lt; 1.5 GB**
- 参考机：8GB RAM / 无 GPU

| 时间 (UTC) | 版本 | 标签 | PID | RSS |
|------------|------|------|-----|-----|
"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if not OUT.is_file():
        OUT.write_text(header + line + "\n", encoding="utf-8")
    else:
        with OUT.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    print(f"pid={args.pid} label={args.label} rss_mb={mb}")
    print(f"wrote {OUT}")
    print(f"host={platform.platform()}")
    # 自测：脚本进程通常远小于 500MB
    if mb is not None and args.label.startswith("idle") and mb >= 500:
        print("WARN: idle sample exceeds 500MB target", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
