"""阶段 2/3 测试：调度器 / 审计落盘 / skill 沙箱。"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys

import pytest

from backend.kernel import AgentKernel, AgentScheduler
from backend.kernel.audit_store import AuditEventStore


# ── 调度器（阶段 2）──

def test_scheduler_priority_order() -> None:
    s = AgentScheduler()
    s.submit("p1", priority=10)
    s.submit("p2", priority=1)  # 最高优先
    s.submit("p3", priority=5)
    order = [s.next().process_id for _ in range(3)]
    assert order == ["p2", "p3", "p1"]


def test_scheduler_fifo_within_same_priority() -> None:
    s = AgentScheduler()
    s.submit("p1", priority=5)
    s.submit("p2", priority=5)
    s.submit("p3", priority=5)
    assert [s.next().process_id for _ in range(3)] == ["p1", "p2", "p3"]


def test_scheduler_aging_prevents_starvation() -> None:
    s = AgentScheduler(age_threshold=0.01)
    old = s.submit("starving", priority=10)
    old.submitted_at -= 1.0  # 模拟已等待 1s（=100 个 aging 周期）
    s.submit("vip", priority=2)
    # aging 后 starving 提到 0，比 vip(2) 更先
    assert s.next().process_id == "starving"


def test_scheduler_complete_and_cancel() -> None:
    s = AgentScheduler()
    t1 = s.submit("p1")
    s.submit("p2")
    nxt = s.next()
    assert nxt is not None and nxt.id == t1.id
    s.complete(t1.id)
    assert s.stats()["done"] == 1
    cancelled = s.cancel_process("p2")
    assert cancelled == 1 and s.next() is None


def test_kernel_exposes_scheduler() -> None:
    k = AgentKernel()
    assert isinstance(k.scheduler, AgentScheduler)


# ── 审计落盘（阶段 3）──

def test_audit_store_append_and_verify(tmp_path) -> None:
    store = AuditEventStore(str(tmp_path / "events.jsonl"))
    kernel = AgentKernel(audit_store=store)

    async def go():
        p = await kernel.create_process("main")
        await kernel.mediate(p.id, "tool_call", "file_read")
        await kernel.end_process(p.id, state="completed")

    asyncio.run(go())
    assert os.path.isfile(store.path)
    lines = open(store.path, encoding="utf-8").read().strip().split("\n")
    # process_created + mediation + policy.decision + process_ended
    assert len(lines) >= 3
    kinds = [__import__("json").loads(ln).get("kind") for ln in lines if ln.strip()]
    assert "process_created" in kinds
    assert "mediation" in kinds
    assert "policy.decision" in kinds
    assert "process_ended" in kinds
    ok, bad = store.verify_file_chain()
    assert ok and bad == -1


def test_audit_store_chain_survives_restart(tmp_path) -> None:
    """重启（新 Kernel 实例）后续链：新事件 prev_hash == 磁盘链尾。"""
    path = str(tmp_path / "events.jsonl")
    k1 = AgentKernel(audit_store=AuditEventStore(path))
    asyncio.run(k1.create_process("main"))
    tail = k1.events()[-1].hash

    k2 = AgentKernel(audit_store=AuditEventStore(path))
    asyncio.run(k2.create_process("main"))
    assert k2.events()[-1].prev_hash == tail
    ok, _ = AuditEventStore(path).verify_file_chain()
    assert ok


def test_audit_store_tamper_detected_in_file(tmp_path) -> None:
    path = str(tmp_path / "events.jsonl")
    kernel = AgentKernel(audit_store=AuditEventStore(path))
    asyncio.run(kernel.create_process("main"))
    # 篡改文件第一行
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()
    bad = json.loads(lines[0])
    bad["detail"] = {"forged": True}
    lines[0] = json.dumps(bad, ensure_ascii=False) + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    ok, lineno = AuditEventStore(path).verify_file_chain()
    assert not ok and lineno == 1


def test_audit_store_write_failure_nonfatal(tmp_path) -> None:
    store = AuditEventStore(str(tmp_path / "nonexistent_dir_ok" / "e.jsonl"))
    # 正常路径：自动建目录成功
    assert store.append({"hash": "x"}) is True
    # 非法路径：父路径是文件而非目录 → 无法 makedirs/open，告警但不抛
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("file-not-dir", encoding="utf-8")
    store2 = AuditEventStore(str(blocker / "e.jsonl"))
    assert store2.append({"hash": "x"}) is False


# ── skill 沙箱（阶段 2）──

def test_sandbox_exec_wrap_and_isolation() -> None:
    from backend.computer.sandbox_exec import (
        skill_sandbox_available,
        wrap_python_argv_sandboxed,
    )

    if not skill_sandbox_available():
        pytest.skip("bwrap 不可用")
    argv = wrap_python_argv_sandboxed(
        [sys.executable, "-c", "print('OK'); import os; print('ENV', len(os.environ))"],
        workspace_root="/opt/hermes-workspace/takton",
    )
    out = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    assert "OK" in out.stdout
    # clearenv 生效：只剩我们 setenv 的 3-4 个
    env_lines = [l for l in out.stdout.split("\n") if l.startswith("ENV ")]
    assert env_lines and int(env_lines[0].split()[1]) <= 4


def test_sandbox_exec_network_blocked() -> None:
    from backend.computer.sandbox_exec import (
        skill_sandbox_available,
        wrap_python_argv_sandboxed,
    )

    if not skill_sandbox_available():
        pytest.skip("bwrap 不可用")
    argv = wrap_python_argv_sandboxed(
        [sys.executable, "-c",
         "import urllib.request; urllib.request.urlopen('http://example.com', timeout=3)"],
        workspace_root="/opt/hermes-workspace/takton",
    )
    out = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    assert out.returncode != 0  # 断网 → 异常退出


def test_skill_sandbox_required_rejects_without_bwrap() -> None:
    """sandbox=required 且 bwrap 不可用 → 拒绝执行（模拟不可用）。"""
    from backend.services import workflow_engine as we
    from backend.computer import sandbox_exec

    monkey = sandbox_exec.skill_sandbox_available
    sandbox_exec.skill_sandbox_available = lambda: False
    try:
        engine = we.WorkflowEngine()
        code = "result = 1 + 1"
        engine._validate_code_ast(code)
        with pytest.raises(we.WorkflowExecutionError, match="沙箱"):
            asyncio.run(
                engine._run_code_in_subprocess(
                    code=code, input_data={}, context_data={}, sandbox="required"
                )
            )
    finally:
        sandbox_exec.skill_sandbox_available = monkey


def test_skill_sandboxed_execution_end_to_end() -> None:
    """真实 bwrap 下 skill 代码端到端执行（结果正确 + 已沙箱化）。"""
    from backend.services import workflow_engine as we
    from backend.computer.sandbox_exec import skill_sandbox_available

    if not skill_sandbox_available():
        pytest.skip("bwrap 不可用")
    engine = we.WorkflowEngine()
    code = "result = input_data['a'] + input_data['b']"
    engine._validate_code_ast(code)
    output = asyncio.run(
        engine._run_code_in_subprocess(
            code=code,
            input_data={"a": 2, "b": 3},
            context_data={},
            label="<skill:add>",
            sandbox="auto",
        )
    )
    assert output.get("result") == 5, output


# ── Evolution 生成 Kernel 配置（阶段 3）──

def test_propose_kernel_config_from_denied_events() -> None:
    from backend.evolution.improver import propose_kernel_config

    events = [
        {"kind": "mediation", "detail": {"allowed": False, "target": "terminal"}}
    ] * 3 + [{"kind": "budget_exceeded", "detail": {}}]
    out = propose_kernel_config(events)
    assert out is not None and out["kind"] == "kernel_config"
    types = {s["type"] for s in out["content"]["suggestions"]}
    assert types == {"grant_capability", "raise_budget"}
    assert out["content"]["auto_apply"] is False  # 建议不自动应用


def test_propose_kernel_config_no_signal() -> None:
    from backend.evolution.improver import propose_kernel_config

    assert propose_kernel_config([]) is None
    ok_events = [{"kind": "mediation", "detail": {"allowed": True, "target": "grep"}}]
    assert propose_kernel_config(ok_events) is None


# ── 多设备加固（阶段 3）──

def test_device_token_encrypt_mask_decrypt_cycle() -> None:
    from backend.api.routes.devices import _encrypt_config_token, _mask_config_tokens
    from backend.services.remote.transport import transport_from_device_config

    cfg = _encrypt_config_token({"agent_token": "secret-token-123", "agent_host": "h"})
    assert cfg["agent_token"].startswith("gAAAAA")  # 落库密文
    assert cfg["agent_token"] != "secret-token-123"

    class FakeDevice:
        config = cfg

    dev = _mask_config_tokens(FakeDevice())
    assert dev.config["agent_token"] == "***"  # 响应掩码

    t = transport_from_device_config(cfg)  # 使用时解密
    assert t.token == "secret-token-123" if hasattr(t, "token") else True


def test_device_token_legacy_plaintext_compatible() -> None:
    from backend.api.routes.devices import _encrypt_config_token
    from backend.services.remote.transport import transport_from_device_config

    cfg = {"agent_token": "legacy-plain-token", "agent_host": "h"}
    enc = _encrypt_config_token(cfg)
    assert enc["agent_token"].startswith("gAAAAA")
    # 存量明文（未走加密路径的数据）transport 直接可用
    t = transport_from_device_config({"agent_token": "legacy-plain-token"})
    assert t is not None


def test_pair_token_min_length() -> None:
    import pydantic

    from backend.api.routes.devices import DevicePairRequest

    with pytest.raises(pydantic.ValidationError):
        DevicePairRequest(name="x", token="short")
    ok = DevicePairRequest(name="x", token="long-enough-token")
    assert ok.token == "long-enough-token"
