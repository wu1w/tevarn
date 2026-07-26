"""跨平台沙箱能力探测（2026-07-26）

统一回答「本机的沙箱模式到底是什么」——manager 分派和 security_check 共用，
避免两处平台逻辑漂移。

能力分级：
- full：完整隔离（Linux bwrap / macOS sandbox-exec / Windows WSL+bwrap）
- restricted：受限模式（Windows Job Object：进程/资源管控，无 FS 隔离）
- none：无沙箱方案
"""
from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class SandboxCapability:
    mode: str  # bwrap | seatbelt | wsl-bwrap | job | none
    level: str  # full | restricted | none
    available: bool
    label: str  # 展示名
    note: str = ""  # 边界说明


def detect_sandbox_capability(platform: str | None = None) -> SandboxCapability:
    """探测当前平台最强可用沙箱。platform 参数仅用于测试注入。"""
    plat = platform if platform is not None else sys.platform

    if plat.startswith("linux"):
        if shutil.which("bwrap"):
            return SandboxCapability("bwrap", "full", True, "bwrap（完整隔离）")
        return SandboxCapability(
            "none", "none", False, "无沙箱", "Linux 请安装 bubblewrap"
        )

    if plat == "darwin":
        from backend.computer.seatbelt_backend import find_sandbox_exec

        if find_sandbox_exec():
            return SandboxCapability(
                "seatbelt", "full", True, "sandbox-exec（完整隔离）"
            )
        return SandboxCapability(
            "none", "none", False, "无沙箱", "macOS sandbox-exec 不可用（系统组件缺失）"
        )

    if plat == "win32":
        from backend.computer.wsl_backend import find_wsl, wsl_has_bwrap

        wsl = find_wsl()
        if wsl and wsl_has_bwrap(wsl):
            return SandboxCapability(
                "wsl-bwrap",
                "full",
                True,
                "WSL bwrap（完整隔离）",
                "沙箱内为 WSL 的 Linux 环境，宿主 Windows 工具链不可用；"
                "适合运行不受信任代码",
            )
        return SandboxCapability(
            "job",
            "restricted",
            True,
            "受限模式（进程/资源管控）",
            "Job Object 提供进程树清理与资源限额，但无文件系统隔离；"
            "安装 WSL2 + bubblewrap 可获得完整隔离",
        )

    return SandboxCapability("none", "none", False, "无沙箱", f"未支持的平台: {plat}")
