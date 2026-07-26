"""Phase 0.5.3 Agent Computer 测试

覆盖：
- LocalBackend：基本执行 / 超时 / 退出码
- BwrapBackend（真 bwrap，C0.5 攻击回归）：
  写 /etc 被拒、宿主凭证不可见、默认断网、cwd 越界清晰报错、workspace 可写
- ComputerManager：per-agent 缓存与 HOME 互不干扰、computer.exec 事件、
  bwrap 缺失清晰报错不降级
- execute_command / execute_python 集成（enabled 走后端 / 失败不静默降级）
"""
import asyncio
import os
import shutil
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest

HAS_BWRAP = shutil.which("bwrap") is not None
needs_bwrap = pytest.mark.skipif(not HAS_BWRAP, reason="bwrap not installed")


@pytest.fixture(autouse=True)
def _reset_computer_manager():
    """每个测试重置全局 ComputerManager 单例，避免 backend 缓存跨测试污染"""
    import backend.computer.manager as mgr_mod

    mgr_mod._manager = None
    yield
    mgr_mod._manager = None


# ═══════════ 1. LocalBackend ═══════════

def test_local_backend_echo(tmp_path):
    from backend.computer.local_backend import LocalBackend

    async def _run():
        be = LocalBackend(str(tmp_path))
        r = await be.run("echo hello", cwd=str(tmp_path))
        assert r.exit_code == 0 and r.stdout == "hello"
        assert r.backend == "local" and not r.sandboxed

        r2 = await be.run("exit 3", cwd=str(tmp_path))
        assert r2.exit_code == 3

    asyncio.run(_run())


def test_local_backend_timeout(tmp_path):
    from backend.computer.local_backend import LocalBackend

    async def _run():
        be = LocalBackend(str(tmp_path))
        r = await be.run("sleep 5", cwd=str(tmp_path), timeout=1)
        assert r.error == "timeout" and r.exit_code == 124

    asyncio.run(_run())


# ═══════════ 2. BwrapBackend（真沙箱） ═══════════

@needs_bwrap
def test_bwrap_workspace_writable(tmp_path):
    from backend.computer.bwrap_backend import BwrapBackend

    async def _run():
        be = BwrapBackend(str(tmp_path), "main")
        r = await be.run("echo ok > w.txt && cat w.txt", cwd=str(tmp_path))
        assert r.exit_code == 0 and r.stdout == "ok"
        assert (tmp_path / "w.txt").read_text().strip() == "ok"

    asyncio.run(_run())


@needs_bwrap
def test_bwrap_etc_write_blocked(tmp_path):
    """C0.5 攻击回归：沙箱内写 /etc 必须失败"""
    from backend.computer.bwrap_backend import BwrapBackend

    async def _run():
        be = BwrapBackend(str(tmp_path), "main")
        r = await be.run("echo pwned >> /etc/passwd", cwd=str(tmp_path))
        assert r.exit_code != 0
        assert "Permission denied" in r.stderr or "Read-only" in r.stderr

    asyncio.run(_run())


@needs_bwrap
def test_bwrap_host_credentials_invisible(tmp_path):
    """C0.5 攻击回归：宿主 HOME/.ssh 不可见（沙箱 HOME 是 per-agent 目录）"""
    from backend.computer.bwrap_backend import BwrapBackend

    real_home = os.path.expanduser("~")

    async def _run():
        be = BwrapBackend(str(tmp_path), "main")
        r = await be.run("echo $HOME", cwd=str(tmp_path))
        assert r.stdout != real_home  # HOME 已换
        r2 = await be.run(f"ls {real_home}/.ssh 2>&1; echo done", cwd=str(tmp_path))
        assert "No such file or directory" in r2.stdout

    asyncio.run(_run())


@needs_bwrap
def test_bwrap_network_off_by_default(tmp_path):
    """C0.5 攻击回归：默认断网"""
    from backend.computer.bwrap_backend import BwrapBackend

    async def _run():
        be = BwrapBackend(str(tmp_path), "main", network=False)
        r = await be.run(
            "python3 -c \"import socket;socket.create_connection(('8.8.8.8',53),2)\"",
            cwd=str(tmp_path),
        )
        assert r.exit_code != 0
        assert "Network is unreachable" in r.stderr

    asyncio.run(_run())


@needs_bwrap
def test_bwrap_cwd_outside_workspace_clear_error(tmp_path):
    from backend.computer.bwrap_backend import BwrapBackend

    async def _run():
        be = BwrapBackend(str(tmp_path), "main")
        r = await be.run("echo hi", cwd="/etc")
        assert r.error and "超出沙箱" in r.error

    asyncio.run(_run())


# ═══════════ 3. ComputerManager ═══════════

def _patch_settings(backend="local"):
    from backend.core.config import settings

    return patch.multiple(
        settings,
        agent_computer_backend=backend,
        agent_computer_network=False,
    )


def test_manager_per_agent_isolation(tmp_path):
    """per-agent computer：不同 key 不同实例；bwrap 时 HOME 目录各自独立"""
    from backend.computer.manager import ComputerManager

    with _patch_settings("local"), patch(
        "backend.tools.permissions.resolve_agent_workspace_root",
        return_value=str(tmp_path),
    ):
        mgr = ComputerManager()
        main = mgr.get_computer("main")
        coder = mgr.get_computer("coder", "Coder")
        assert main is mgr.get_computer("main")  # 缓存复用
        assert main is not coder
        assert coder.agent_label == "Coder"
        assert len(mgr.list_computers()) == 2


@needs_bwrap
def test_manager_bwrap_home_isolation_between_agents(tmp_path):
    """子代理互不干扰实测：A 写的 HOME 文件 B 看不见"""
    from backend.computer.manager import ComputerManager

    with _patch_settings("bwrap"), patch(
        "backend.tools.permissions.resolve_agent_workspace_root",
        return_value=str(tmp_path),
    ):
        mgr = ComputerManager()

        async def _run():
            out_a = await mgr.execute("echo secret > $HOME/s.txt", agent_key="a")
            assert "[Exit 0" in out_a
            out_b = await mgr.execute("cat $HOME/s.txt 2>&1; echo done", agent_key="b")
            assert "No such file or directory" in out_b
            # 但 workspace 是共享的（项目协作语义）
            await mgr.execute("echo shared > proj.txt", agent_key="a")
            out_b2 = await mgr.execute("cat proj.txt", agent_key="b")
            assert "shared" in out_b2

        asyncio.run(_run())


def test_manager_publishes_computer_exec_events(tmp_path):
    from backend.computer.manager import ComputerManager
    from backend.core.event_bus import event_bus

    events: list[dict] = []

    async def capture(topic, payload):
        events.append(payload)

    async def _run():
        unsub = event_bus.subscribe("computer.*", capture)
        try:
            mgr = ComputerManager()
            rc = SimpleNamespace(run_id=uuid.uuid4())
            await mgr.execute(
                "echo hi",
                agent_key="main",
                agent_label="主 Agent",
                session_id="sid-1",
                recorder=rc,
            )
        finally:
            unsub()

    with _patch_settings("local"), patch(
        "backend.tools.permissions.resolve_agent_workspace_root",
        return_value=str(tmp_path),
    ):
        asyncio.run(_run())

    assert [e["phase"] for e in events] == ["start", "end"]
    assert events[0]["agent_key"] == "main"
    assert events[0]["session_id"] == "sid-1"
    assert events[1]["exit_code"] == 0
    assert events[1]["run_id"] is not None


def test_manager_bwrap_missing_clear_error(tmp_path):
    """bwrap 缺失：清晰报错，不静默降级"""
    from backend.computer.manager import ComputerManager

    with _patch_settings("bwrap"), patch(
        "backend.tools.permissions.resolve_agent_workspace_root",
        return_value=str(tmp_path),
    ), patch("shutil.which", return_value=None):
        mgr = ComputerManager()
        with pytest.raises(RuntimeError, match="bwrap 未安装"):
            mgr.get_computer("main")


# ═══════════ 4. 工具集成 ═══════════

def test_execute_command_via_computer_local(tmp_path):
    """agent_computer_enabled=True：command 走 computer 后端并带 sandbox 标记"""
    from backend.core.config import settings
    from backend.services.tools.executors import execute_command

    with patch.multiple(
        settings,
        agent_computer_enabled=True,
        agent_computer_backend="local",
        agent_computer_network=False,
    ), patch(
        "backend.tools.permissions.resolve_agent_workspace_root",
        return_value=str(tmp_path),
    ):
        out = asyncio.run(
            execute_command({}, {"command": "echo via-computer", "cwd": str(tmp_path)})
        )
        assert "via-computer" in out


def test_execute_command_computer_failure_no_silent_fallback(tmp_path):
    """computer 失败（bwrap 缺失）→ 清晰错误，绝不静默跑本地"""
    from backend.core.config import settings
    from backend.services.tools.executors import execute_command

    with patch.multiple(
        settings,
        agent_computer_enabled=True,
        agent_computer_backend="bwrap",
        agent_computer_network=False,
    ), patch(
        "backend.tools.permissions.resolve_agent_workspace_root",
        return_value=str(tmp_path),
    ), patch("shutil.which", return_value=None):
        out = asyncio.run(
            execute_command({}, {"command": "echo should-not-run", "cwd": str(tmp_path)})
        )
        # 文案在 T5 统一为「沙箱执行失败」；不变的是意图：报错而非偷偷跑本机
        assert out.startswith("[Error] 沙箱执行失败")
        assert "should-not-run" not in out
        # 必须指引用户去权限控制台改「执行环境」，而不是让他猜配置键
        assert "权限控制台" in out


def test_execute_python_via_computer(tmp_path):
    from backend.core.config import settings
    from backend.services.tools.executors import execute_python

    with patch.multiple(
        settings,
        agent_computer_enabled=True,
        agent_computer_backend="local",
        agent_computer_network=False,
    ), patch(
        "backend.tools.permissions.resolve_agent_workspace_root",
        return_value=str(tmp_path),
    ):
        out = asyncio.run(execute_python({}, {"code": "print(40 + 2)"}))
        assert "42" in out
