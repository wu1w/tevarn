"""T1：同轮只读工具并发执行。

system_prompt 的 PARALLEL_TOOL_CALLS 段向模型承诺
「the runtime executes independent calls concurrently」，
此前 tool_round 是纯串行 for 循环 —— 模型照做批量请求反而更慢。

这里锁定的是**正确性**（顺序 / 隔离 / 失败语义），速度只做一条下限断言。
"""

import asyncio
import uuid

import pytest

from backend.agent.phases.tool_round import _prefetch_readonly_calls
from backend.tools.base import ToolRiskLevel


class _Call:
    def __init__(self, cid, name, arguments=None):
        self.id = cid
        self.name = name
        self.arguments = arguments or {}


class _Tool:
    def __init__(self, risk):
        self.risk_level = risk
        self.parameters = {"type": "object", "properties": {}}


class _Loop:
    """最小 loop 替身：只暴露 _prefetch_readonly_calls 用到的表面。"""

    user_id = None
    ws_manager = None

    def __init__(self, delay=0.05, fail=None):
        self.delay = delay
        self.fail = fail or {}
        self.calls = []
        self.warmups = []

    def _validate_tool_args(self, schema, args):
        return dict(args or {})

    async def _contract_tool_block_reason(self, name, args):
        self.warmups.append(name)
        return None

    async def _execute_registered_tool(self, name, args):
        self.calls.append(name)
        await asyncio.sleep(self.delay)
        if name in self.fail:
            raise self.fail[name]
        return f"result::{name}"


@pytest.fixture
def registry(monkeypatch):
    table = {}

    class _Reg:
        @staticmethod
        def get(name):
            return table.get(name)

    monkeypatch.setattr("backend.tools.registry.ToolRegistry", _Reg)
    return table


async def _prefetch(loop, calls):
    return await _prefetch_readonly_calls(
        loop, session_id=uuid.uuid4(), mode="default", tool_calls=calls
    )


@pytest.mark.asyncio
async def test_readonly_batch_runs_concurrently(registry):
    registry["file_read"] = _Tool(ToolRiskLevel.SAFE)
    registry["grep"] = _Tool(ToolRiskLevel.SAFE)
    registry["glob"] = _Tool(ToolRiskLevel.SAFE)
    loop = _Loop(delay=0.1)

    calls = [_Call("a", "file_read"), _Call("b", "grep"), _Call("c", "glob")]
    t0 = asyncio.get_event_loop().time()
    out = await _prefetch(loop, calls)
    elapsed = asyncio.get_event_loop().time() - t0

    assert set(out) == {"a", "b", "c"}
    assert out["a"] == ("result::file_read", None)
    # 串行需 0.3s；并发应显著低于 1.5 倍单次耗时
    assert elapsed < 0.15, f"expected concurrency, took {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_write_in_batch_forces_whole_batch_serial(registry):
    """并发读 + 写同一文件会读到中间态，故整批退回串行。"""
    registry["file_read"] = _Tool(ToolRiskLevel.SAFE)
    registry["file_write"] = _Tool(ToolRiskLevel.HIGH)
    loop = _Loop()

    out = await _prefetch(loop, [_Call("a", "file_read"), _Call("b", "file_write")])

    assert out == {}
    assert loop.calls == []  # 一个都没预跑，全部留给串行主体


@pytest.mark.asyncio
async def test_command_never_prefetched(registry):
    registry["command"] = _Tool(ToolRiskLevel.DANGEROUS)
    registry["file_read"] = _Tool(ToolRiskLevel.SAFE)
    loop = _Loop()

    out = await _prefetch(loop, [_Call("a", "command"), _Call("b", "file_read")])
    assert out == {}


@pytest.mark.asyncio
async def test_unknown_tool_forces_serial(registry):
    """未注册工具走的是 DB skill 回退路径，不能预取。"""
    registry["file_read"] = _Tool(ToolRiskLevel.SAFE)
    loop = _Loop()

    out = await _prefetch(loop, [_Call("a", "file_read"), _Call("b", "mystery_skill")])
    assert out == {}


@pytest.mark.asyncio
async def test_single_call_not_parallelised(registry):
    registry["file_read"] = _Tool(ToolRiskLevel.SAFE)
    loop = _Loop()

    out = await _prefetch(loop, [_Call("a", "file_read")])
    assert out == {}
    assert loop.calls == []


@pytest.mark.asyncio
async def test_exception_is_carried_back_not_swallowed(registry):
    """并行失败必须原样带回串行主体重抛，失败语义与串行一致。"""
    registry["file_read"] = _Tool(ToolRiskLevel.SAFE)
    registry["grep"] = _Tool(ToolRiskLevel.SAFE)
    boom = RuntimeError("disk on fire")
    loop = _Loop(delay=0.01, fail={"grep": boom})

    out = await _prefetch(loop, [_Call("a", "file_read"), _Call("b", "grep")])

    assert out["a"] == ("result::file_read", None)
    assert out["b"][1] is boom
    # 一个失败不影响另一个的结果
    assert out["a"][0] == "result::file_read"


@pytest.mark.asyncio
async def test_timeout_is_carried_back(registry, monkeypatch):
    from backend.core.config import settings

    monkeypatch.setattr(settings, "agent_tool_timeout_seconds", 0.02, raising=False)
    registry["file_read"] = _Tool(ToolRiskLevel.SAFE)
    registry["grep"] = _Tool(ToolRiskLevel.SAFE)
    loop = _Loop(delay=0.5)

    out = await _prefetch(loop, [_Call("a", "file_read"), _Call("b", "grep")])

    assert isinstance(out["a"][1], asyncio.TimeoutError)
    assert isinstance(out["b"][1], asyncio.TimeoutError)


@pytest.mark.asyncio
async def test_parallelism_is_bounded(registry, monkeypatch):
    """并发度必须受限，否则 20 个 http 会打爆下游。"""
    from backend.core.config import settings

    monkeypatch.setattr(settings, "agent_tool_parallel_max", 2, raising=False)
    registry["http"] = _Tool(ToolRiskLevel.SAFE)

    live = 0
    peak = 0

    class _Tracking(_Loop):
        async def _execute_registered_tool(self, name, args):
            nonlocal live, peak
            live += 1
            peak = max(peak, live)
            await asyncio.sleep(0.05)
            live -= 1
            return "ok"

    loop = _Tracking()
    await _prefetch(loop, [_Call(str(i), "http") for i in range(6)])
    assert peak <= 2


@pytest.mark.asyncio
async def test_kill_switch_restores_serial(registry, monkeypatch):
    from backend.core.config import settings

    monkeypatch.setattr(settings, "agent_tool_parallel", False, raising=False)
    registry["file_read"] = _Tool(ToolRiskLevel.SAFE)
    loop = _Loop()

    out = await _prefetch(loop, [_Call("a", "file_read"), _Call("b", "file_read")])
    assert out == {}
    assert loop.calls == []


@pytest.mark.asyncio
async def test_contract_whitelist_is_warmed_before_concurrency(registry):
    """契约白名单必须在并发前预热，否则首批并发会绕过 skill 契约拦截。"""
    registry["file_read"] = _Tool(ToolRiskLevel.SAFE)
    registry["grep"] = _Tool(ToolRiskLevel.SAFE)
    loop = _Loop(delay=0.01)

    await _prefetch(loop, [_Call("a", "file_read"), _Call("b", "grep")])
    assert loop.warmups, "预热未发生：并发首调可能绕过契约白名单"


@pytest.mark.asyncio
async def test_results_key_by_tool_call_id_not_name(registry):
    """同名工具的多次调用必须各自可寻址，否则 messages 会串位。"""
    registry["file_read"] = _Tool(ToolRiskLevel.SAFE)
    loop = _Loop(delay=0.01)

    calls = [
        _Call("id1", "file_read", {"filepath": "a.py"}),
        _Call("id2", "file_read", {"filepath": "b.py"}),
        _Call("id3", "file_read", {"filepath": "c.py"}),
    ]
    out = await _prefetch(loop, calls)
    assert set(out) == {"id1", "id2", "id3"}
