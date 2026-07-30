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

    async def test_run_without_sandbox_exec_friendly_error(self, tmp_path):
        """sandbox-exec 不可用时返回明确错误而非崩溃。

        原实现依赖「跑在 Linux 上、sandbox-exec 必然缺失」这一宿主前提，
        在 macOS 上会因该二进制真实存在而失败。改为显式注入不存在的路径，
        直接覆盖 FileNotFoundError 分支，任何平台结果一致。
        """
        b = SeatbeltBackend(
            str(tmp_path), "main", sandbox_exec_path="/nonexistent/sandbox-exec"
        )
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
    """平台能力探测：全部 mock 掉宿主探测，使分支逻辑在任何开发机上结果一致。

    原实现假设「开发机是装了 bwrap 的 Linux」，在 macOS / Windows 上必然失败 ——
    那是在测机器而不是测代码。
    """

    def test_linux_with_bwrap(self, monkeypatch):
        monkeypatch.setattr(
            "backend.computer.detect.shutil.which",
            lambda name: "/usr/bin/bwrap" if name == "bwrap" else None,
        )
        cap = detect_sandbox_capability("linux")
        assert cap.mode == "bwrap" and cap.level == "full" and cap.available

    def test_linux_without_bwrap_has_actionable_note(self, monkeypatch):
        monkeypatch.setattr("backend.computer.detect.shutil.which", lambda name: None)
        cap = detect_sandbox_capability("linux")
        assert cap.mode == "none" and not cap.available
        assert "bubblewrap" in cap.note  # 必须给出安装指引

    def test_darwin_with_sandbox_exec(self, monkeypatch):
        monkeypatch.setattr(
            "backend.computer.seatbelt_backend.find_sandbox_exec",
            lambda: "/usr/bin/sandbox-exec",
        )
        cap = detect_sandbox_capability("darwin")
        assert cap.mode == "seatbelt" and cap.level == "full" and cap.available

    def test_darwin_without_sandbox_exec(self, monkeypatch):
        monkeypatch.setattr(
            "backend.computer.seatbelt_backend.find_sandbox_exec", lambda: None
        )
        cap = detect_sandbox_capability("darwin")
        assert cap.mode == "none" and not cap.available

    def test_win32_without_wsl_falls_back_to_job(self, monkeypatch):
        monkeypatch.setattr("backend.computer.wsl_backend.find_wsl", lambda: None)
        cap = detect_sandbox_capability("win32")
        assert cap.mode == "job" and cap.level == "restricted" and cap.available
        assert "WSL2" in cap.note  # 引导升级路径

    def test_manager_auto_dispatch_to_bwrap(self, monkeypatch):
        """auto 模式按 detect 结果分派到 BwrapBackend。"""
        from backend.computer.bwrap_backend import BwrapBackend
        from backend.computer.detect import SandboxCapability
        from backend.computer.manager import ComputerManager

        monkeypatch.setattr(
            "backend.computer.detect.detect_sandbox_capability",
            lambda platform=None: SandboxCapability("bwrap", "full", True, "bwrap"),
        )
        monkeypatch.setattr(
            "backend.computer.manager.shutil.which", lambda name: "/usr/bin/bwrap"
        )
        assert isinstance(ComputerManager()._make_backend("main"), BwrapBackend)

    def test_manager_auto_with_no_sandbox_raises(self, monkeypatch):
        """无可用沙箱时必须报错，不得静默退回本机直跑。"""
        from backend.computer.detect import SandboxCapability
        from backend.computer.manager import ComputerManager

        monkeypatch.setattr(
            "backend.computer.detect.detect_sandbox_capability",
            lambda platform=None: SandboxCapability("none", "none", False, "无沙箱"),
        )
        with pytest.raises(RuntimeError, match="无可用沙箱"):
            ComputerManager()._make_backend("main")

    def test_manager_explicit_seatbelt_without_binary_errors(self, monkeypatch):
        """显式指定 seatbelt 但二进制不可用：明确报错。"""
        from backend.computer.manager import ComputerManager
        from backend.core.config import settings

        monkeypatch.setattr(
            "backend.computer.seatbelt_backend.find_sandbox_exec", lambda: None
        )
        monkeypatch.setattr(
            settings, "agent_computer_backend", "seatbelt", raising=False
        )
        with pytest.raises(RuntimeError, match="sandbox-exec"):
            ComputerManager()._make_backend("main")
