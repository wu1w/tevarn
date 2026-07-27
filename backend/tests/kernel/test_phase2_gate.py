"""Phase 2 kernel↔loop 融合测试（Alpha Review #1）。

三件事：
- #1a 事前预算检查：LLM 调用前预估消耗，剩余不足即中断（事后 charge 之外的刹车）
- #1b suspend/resume：进程挂起 → loop iteration gate 阻塞等待恢复
- #1c 调度让出：gate 内 asyncio.sleep(0) 公平性语义点

零 mock 红线：loop 用裸实例（object.__new__）直接测 gate 纯逻辑，
kernel/process 用真实对象。
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from backend.agent.loop import NexusAgentLoop
from backend.kernel import AgentKernel


def _bare_loop() -> NexusAgentLoop:
    loop = object.__new__(NexusAgentLoop)
    loop._should_stop = False
    loop.ws_manager = None
    loop.event_sink = None
    return loop


def _run(coro):
    return asyncio.run(coro)


# ── #1b process 状态机 ─────────────────────────────────────────


def test_process_suspend_resume_state_machine() -> None:
    from backend.kernel.process import AgentProcess

    proc = AgentProcess(identity="main")
    proc.state = "running"

    proc.suspend()
    assert proc.state == "suspended"
    proc.suspend()  # 幂等
    assert proc.state == "suspended"

    proc.resume()
    assert proc.state == "running"
    proc.resume()  # 非挂起幂等
    assert proc.state == "running"


def test_process_suspend_terminal_rejected() -> None:
    from backend.kernel.process import AgentProcess

    proc = AgentProcess(identity="main")
    proc.state = "completed"
    with pytest.raises(ValueError, match="已终止"):
        proc.suspend()


def test_wait_if_suspended_blocks_until_resume() -> None:
    from backend.kernel.process import AgentProcess

    async def go():
        proc = AgentProcess(identity="main")
        proc.state = "running"
        proc.suspend()

        woke = []

        async def waiter():
            ok = await proc.wait_if_suspended(poll=0.05)
            woke.append(ok)

        t = asyncio.create_task(waiter())
        await asyncio.sleep(0.15)
        assert not woke  # 挂起中：阻塞
        proc.resume()
        await asyncio.wait_for(t, timeout=2)
        assert woke == [True]  # 恢复后放行

    _run(go())


def test_wait_if_suspended_should_stop_breaks() -> None:
    from backend.kernel.process import AgentProcess

    async def go():
        proc = AgentProcess(identity="main")
        proc.state = "running"
        proc.suspend()
        ok = await proc.wait_if_suspended(poll=0.05, should_stop=lambda: True)
        assert ok is False  # stop 打断 → 调用方应中止

    _run(go())


def test_kernel_suspend_resume_process_events() -> None:
    async def go():
        k = AgentKernel()
        proc = await k.create_process("main", session_id=None)
        await k.mark_running(proc.id)

        await k.suspend_process(proc.id, reason="人工暂停")
        assert k.get_process(proc.id).state == "suspended"
        assert k.get_process(proc.id).meta["suspend_reason"] == "人工暂停"

        await k.resume_process(proc.id)
        p = k.get_process(proc.id)
        assert p.state == "running"
        assert "suspend_reason" not in p.meta

        kinds = [e.kind for e in k.events()]
        assert "process_suspended" in kinds
        assert "process_resumed" in kinds

    _run(go())


# ── #1a/#1c loop iteration gate ────────────────────────────────


def test_gate_no_process_passthrough() -> None:
    loop = _bare_loop()
    loop._kernel_process = None
    out = _run(loop._kernel_iteration_gate(uuid.uuid4(), []))
    assert out is None


def test_gate_budget_precheck_blocks() -> None:
    """剩余预算 < 预估消耗 → 'budget'（事前中断）。"""
    from backend.kernel.process import AgentProcess

    loop = _bare_loop()
    proc = AgentProcess(identity="main", token_budget=100)  # 极小预算
    proc.state = "running"
    proc.charge_tokens(50)  # 剩 50
    loop._kernel_process = proc

    big_ctx = [{"role": "user", "content": "x" * 3000}]  # ≈ 882 + 2000 预留
    out = _run(loop._kernel_iteration_gate(uuid.uuid4(), big_ctx))
    assert out == "budget"


def test_gate_budget_precheck_passes_when_sufficient() -> None:
    from backend.kernel.process import AgentProcess

    loop = _bare_loop()
    proc = AgentProcess(identity="main", token_budget=1_000_000)
    proc.state = "running"
    loop._kernel_process = proc

    out = _run(loop._kernel_iteration_gate(uuid.uuid4(), [{"role": "user", "content": "hi"}]))
    assert out is None


def test_gate_no_budget_limit_skips_precheck() -> None:
    """token_budget=None（不限）→ 事前检查不生效。"""
    from backend.kernel.process import AgentProcess

    loop = _bare_loop()
    proc = AgentProcess(identity="main", token_budget=None)
    proc.state = "running"
    loop._kernel_process = proc

    big_ctx = [{"role": "user", "content": "x" * 100_000}]
    out = _run(loop._kernel_iteration_gate(uuid.uuid4(), big_ctx))
    assert out is None


def test_gate_suspended_waits_then_continues() -> None:
    """#1b：挂起的进程在 gate 阻塞；kernel resume 后 gate 放行。"""
    from backend.kernel.process import AgentProcess

    async def go():
        loop = _bare_loop()
        proc = AgentProcess(identity="main", token_budget=None)
        proc.state = "running"
        proc.suspend()
        loop._kernel_process = proc

        t = asyncio.create_task(loop._kernel_iteration_gate(uuid.uuid4(), []))
        await asyncio.sleep(0.2)
        assert not t.done()  # gate 阻塞中
        proc.resume()
        out = await asyncio.wait_for(t, timeout=2)
        assert out is None  # 恢复后放行

    _run(go())


def test_gate_suspended_stop_breaks_run() -> None:
    """挂起期间收到 stop → gate 返回 'stop'，run 退出。"""
    from backend.kernel.process import AgentProcess

    async def go():
        loop = _bare_loop()
        proc = AgentProcess(identity="main", token_budget=None)
        proc.state = "running"
        proc.suspend()
        loop._kernel_process = proc

        async def stopper():
            await asyncio.sleep(0.15)
            loop._should_stop = True

        asyncio.create_task(stopper())
        out = await asyncio.wait_for(
            loop._kernel_iteration_gate(uuid.uuid4(), []), timeout=3
        )
        assert out == "stop"

    _run(go())


def test_estimate_next_call_tokens() -> None:
    loop = _bare_loop()
    # 空上下文：min 1 + 预留
    assert loop._estimate_next_call_tokens([]) == 2001
    # 3400 字符 ≈ 1000 + 2000 预留
    msgs = [{"role": "user", "content": "a" * 3400}]
    assert loop._estimate_next_call_tokens(msgs) == 3000
    # 多模态分块计入
    mm = [{"role": "user", "content": [{"type": "text", "text": "b" * 340}]}]
    assert loop._estimate_next_call_tokens(mm) == 2100
