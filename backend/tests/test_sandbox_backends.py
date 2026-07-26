"""跨平台沙箱后端测试（macOS seatbelt / Windows job / WSL bwrap，2026-07-26）

零 mock 原则下的取舍：macOS/Windows 原生执行层无法在本机（Linux）真实运行，
本文件验证的是**生成逻辑**（profile/argv/路径映射/平台分派）与**错误路径**，
真实执行实测点已记录（见 CURRENT_TASK.md）。
"""

import pytest

from backend.computer.detect import detect_sandbox_capability
from backend.computer.job_backend import JobBackend
from backend.computer.seatbelt_backend import (
    SeatbeltBackend,
    build_seatbelt_profile,
)
from backend.computer.wsl_backend import WslBwrapBackend, win_path_to_wsl


# ---------- seatbelt profile 生成 ----------


class TestSeatbeltProfile:
    def test_profile_structure(self):
        p = build_seatbelt_profile("/Users/x/proj", "/Users/x/proj/.computers/main/home")
        assert "(deny default)" in p
        assert "(allow file-read*)" in p  # 宿主工具链可读
        assert '(subpath "/Users/x/proj")' in p  # workspace 可写
        assert "(allow process-exec process-fork)" in p
        # 默认断网（仅 loopback）
        assert "(allow network*)" not in p
        assert "(local ip)" in p

    def test_profile_network_open(self):
        p = build_seatbelt_profile("/tmp/ws", "/tmp/ws/.computers/a/home", network=True)
        assert "(allow network*)" in p

    def test_profile_path_escaping(self):
        # 含引号/反斜杠的路径必须转义，否则 profile 注入
        p = build_seatbelt_profile('/tmp/evil"path', "/tmp/evil path/.computers/a/home")
        assert '\\"' in p
        assert 'evil"path' not in p

    def test_argv_wraps_cd_and_env(self):
        b = SeatbeltBackend("/tmp/ws", "main", sandbox_exec_path="/usr/bin/sandbox-exec")
        argv = b._build_argv("echo hi", "/tmp/ws")
        assert argv[0] == "/usr/bin/sandbox-exec"
        assert "-p" in argv
        joined = " ".join(argv)
        assert "env" in joined and "-i" in argv  # 最小环境
        assert "HOME=/tmp/ws/.computers/main/home" in argv
        assert "cd /tmp/ws && echo hi" in argv  # cwd 包装

    def test_cwd_outside_workspace_rejected(self):
        b = SeatbeltBackend("/tmp/ws", "main")
        assert b._check_cwd("/etc") is not None
        assert b._check_cwd("/tmp/ws/sub") is None

    async def test_run_on_linux_friendly_error(self, tmp_path):
        """Linux 上无 sandbox-exec：返回明确错误而非崩溃。"""
        b = SeatbeltBackend(str(tmp_path), "main")
        res = await b.run("echo hi", cwd=str(tmp_path))
        assert res.exit_code == 127
        assert "sandbox-exec" in (res.error or "")


# ---------- WSL 路径映射 + argv ----------


class TestWslBackend:
    @pytest.mark.parametrize(
        "win,wsl",
        [
            ("C:\\Users\\x\\proj", "/mnt/c/Users/x/proj"),
            ("D:\\work", "/mnt/d/work"),
            ("C:/mixed/slash", "/mnt/c/mixed/slash"),
            ("c:\\lower", "/mnt/c/lower"),
        ],
    )
    def test_drive_mapping(self, win, wsl):
        assert win_path_to_wsl(win) == wsl

    def test_non_drive_passthrough(self):
        # 非盘符路径只做分隔符规整
        assert "\\" not in win_path_to_wsl("\\\\server\\share\\dir")

    def test_argv_uses_wsl_paths(self):
        b = WslBwrapBackend("C:\\Users\\x\\proj", "main", wsl_path="wsl.exe")
        argv = b._build_argv("pytest", "C:\\Users\\x\\proj")
        assert argv[0:2] == ["wsl.exe", "-e"]
        assert "bwrap" in argv
        joined = " ".join(argv)
        assert "--bind /mnt/c/Users/x/proj /mnt/c/Users/x/proj" in joined
        assert "--chdir /mnt/c/Users/x/proj" in joined
        assert "--clearenv" in argv
        assert "--unshare-net" in argv  # 默认断网
        # 不应残留任何 Windows 路径
        assert "C:\\" not in joined

    def test_cwd_outside_workspace_rejected(self):
        b = WslBwrapBackend("C:\\proj", "main")
        assert b._check_cwd("C:\\Windows") is not None
        assert b._check_cwd("C:\\proj\\sub") is None


# ---------- Job backend（Windows 受限模式）----------


class TestJobBackend:
    def test_cwd_check(self):
        b = JobBackend("C:\\proj", "main")
        assert b._check_cwd("C:\\proj") is None
        assert b._check_cwd("C:\\other") is not None

    async def test_run_on_non_windows_friendly_error(self, tmp_path):
        """非 Windows：明确报错（执行层未触发 ctypes）。"""
        b = JobBackend(str(tmp_path), "main")
        res = await b.run("echo hi", cwd=str(tmp_path))
        assert res.exit_code == 127
        assert "Windows" in (res.error or "")


# ---------- 平台能力探测 + manager 分派 ----------


class TestDetect:
    def test_linux_with_bwrap(self):
        # 本机（Linux + bwrap 已装）：full
        cap = detect_sandbox_capability("linux")
        assert cap.mode == "bwrap" and cap.level == "full" and cap.available

    def test_darwin_without_sandbox_exec_on_linux(self):
        # 在 Linux 上探测 darwin 分支：sandbox-exec 不存在 → none
        cap = detect_sandbox_capability("darwin")
        assert cap.mode == "none" and not cap.available

    def test_win32_without_wsl_falls_back_to_job(self):
        # 在 Linux 上探测 win32 分支：无 wsl.exe → Job 受限模式
        cap = detect_sandbox_capability("win32")
        assert cap.mode == "job" and cap.level == "restricted" and cap.available
        assert "WSL2" in cap.note  # 引导升级路径

    def test_manager_auto_dispatch_linux(self, tmp_path):
        """auto 模式在本机（Linux+bwrap）分派到 BwrapBackend。"""
        from backend.computer.manager import ComputerManager

        mgr = ComputerManager()
        backend = mgr._make_backend("main")
        from backend.computer.bwrap_backend import BwrapBackend

        assert isinstance(backend, BwrapBackend)

    def test_manager_explicit_seatbelt_on_linux_errors(self, tmp_path):
        """显式指定 seatbelt 但非 macOS：明确报错。"""
        from backend.computer.manager import ComputerManager
        from backend.core.config import settings

        orig = settings.agent_computer_backend
        settings.agent_computer_backend = "seatbelt"
        try:
            mgr = ComputerManager()
            with pytest.raises(RuntimeError, match="sandbox-exec"):
                mgr._make_backend("main")
        finally:
            settings.agent_computer_backend = orig
