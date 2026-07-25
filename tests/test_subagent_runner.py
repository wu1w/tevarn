"""Phase 1 真 Sub-Agent（迷你 Run）测试

覆盖：
- snapshot_for_model_ref 解析
- run_subagent：child loop 配置（agent_key/label/LLM 快照覆盖/深度/轮次）、
  persona+goal+context 进 prompt、mode=subagent、_nested=True、结果包装
- 嵌套深度上限拒绝
- 超时返回 [Timeout]（不炸父 run）
- loop._nested=True 旁路 session 锁（父持锁时不死锁）
- DelegateTaskTool action=run 走 run_subagent 并透传 depth/parent_run_id
"""
import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


# ═══════════ 1. model_ref 快照 ═══════════

def test_snapshot_for_model_ref():
    from backend.agent.subagent_runner import snapshot_for_model_ref

    assert snapshot_for_model_ref("prov-x/model-y") == {
        "provider": "prov-x", "provider_id": "prov-x", "model": "model-y",
    }
    assert snapshot_for_model_ref("") is None
    assert snapshot_for_model_ref("no-slash") is None
    assert snapshot_for_model_ref("prov/") is None
    assert snapshot_for_model_ref("/model") is None


# ═══════════ 2. run_subagent ═══════════

def _fake_agent(**over):
    base = dict(
        id="a1",
        name="Coder",
        description="写代码的",
        system_prompt="你是资深工程师",
        model_ref="prov-x/model-y",
        max_iterations=7,
    )
    base.update(over)
    return SimpleNamespace(**base)


class _FakeChild:
    """捕获配置的假 NexusAgentLoop"""

    instances: list = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.max_iterations = 25
        self.run_args: dict = {}
        _FakeChild.instances.append(self)

    async def run(self, session_id, user_input, attachments=None, mode="default", _nested=False):
        self.run_args = {
            "session_id": session_id,
            "user_input": user_input,
            "mode": mode,
            "_nested": _nested,
        }
        return "mini result"


def _patch_child_loop(child_cls=None):
    """patch runner 内部的 NexusAgentLoop 与依赖注入"""
    child_cls = child_cls or _FakeChild
    _FakeChild.instances = []
    deps = {
        f"backend.api.dependencies.get_{name}_repo": AsyncMock(return_value=SimpleNamespace())
        for name in (
            "session", "message", "task", "ctx_item", "context_flow", "notification"
        )
    }
    patches = [patch("backend.agent.NexusAgentLoop", child_cls)]
    patches += [patch(k, v) for k, v in deps.items()]
    return patches


def test_run_subagent_configures_child():
    from backend.agent.subagent_runner import run_subagent

    patches = _patch_child_loop()
    for p in patches:
        p.start()
    try:
        sid = uuid.uuid4()
        parent_rid = uuid.uuid4()
        out = asyncio.run(run_subagent(
            session_id=sid,
            sub_agent=_fake_agent(),
            goal="写一个快排",
            context="项目用 Python 3.11",
            parent_run_id=parent_rid,
            depth=0,
        ))
    finally:
        for p in patches:
            p.stop()

    assert out == "[delegate_task -> Coder]\nmini result"
    child = _FakeChild.instances[0]
    # agent 身份（Agent Computer 沙箱隔离）
    assert child._agent_key == "sub:a1"
    assert child._agent_label == "Coder"
    # LLM 快照覆盖（model_ref 生效）
    assert child._llm_snapshot_override == {
        "provider": "prov-x", "provider_id": "prov-x", "model": "model-y",
    }
    # 深度 / 轮次 / 父 run 溯源
    assert child._subagent_depth == 1
    assert child._parent_run_id == parent_rid
    assert child.max_iterations == 7
    # run 调用形态
    ra = child.run_args
    assert ra["mode"] == "subagent"
    assert ra["_nested"] is True
    assert ra["session_id"] == sid
    # persona + goal + context 进 prompt
    assert "你是资深工程师" in ra["user_input"]
    assert "写一个快排" in ra["user_input"]
    assert "Python 3.11" in ra["user_input"]


def test_run_subagent_no_model_ref_no_override():
    from backend.agent.subagent_runner import run_subagent

    patches = _patch_child_loop()
    for p in patches:
        p.start()
    try:
        asyncio.run(run_subagent(
            session_id=uuid.uuid4(),
            sub_agent=_fake_agent(model_ref=""),
            goal="g",
        ))
    finally:
        for p in patches:
            p.stop()

    child = _FakeChild.instances[0]
    assert not hasattr(child, "_llm_snapshot_override")


def test_run_subagent_depth_guard():
    from backend.agent.subagent_runner import run_subagent

    patches = _patch_child_loop()
    for p in patches:
        p.start()
    try:
        out = asyncio.run(run_subagent(
            session_id=uuid.uuid4(),
            sub_agent=_fake_agent(),
            goal="g",
            depth=1,  # 默认 max_depth=1
        ))
    finally:
        for p in patches:
            p.stop()

    assert out.startswith("[Error] 子代理嵌套已达上限")
    assert len(_FakeChild.instances) == 0  # 未构造子 loop


def test_run_subagent_timeout_returns_gracefully():
    from backend.agent.subagent_runner import run_subagent
    from backend.core.config import settings

    class _SlowChild(_FakeChild):
        async def run(self, *a, **kw):
            await asyncio.sleep(10)
            return "never"

    patches = _patch_child_loop(_SlowChild)
    for p in patches:
        p.start()
    old = settings.agent_subagent_timeout_seconds
    settings.agent_subagent_timeout_seconds = 0.1
    try:
        out = asyncio.run(asyncio.wait_for(run_subagent(
            session_id=uuid.uuid4(),
            sub_agent=_fake_agent(),
            goal="g",
        ), timeout=5))
    finally:
        settings.agent_subagent_timeout_seconds = old
        for p in patches:
            p.stop()

    assert out.startswith("[Timeout] 子代理")


# ═══════════ 3. _nested 旁路 session 锁 ═══════════

def test_nested_run_bypasses_session_lock():
    """父 run 持锁时，_nested=True 必须直接执行（否则自死锁）"""
    from backend.agent import loop as loop_mod

    agent = loop_mod.NexusAgentLoop(
        session_repo=SimpleNamespace(),
        message_repo=SimpleNamespace(),
        task_repo=SimpleNamespace(),
        ctx_item_repo=SimpleNamespace(),
        context_flow_repo=SimpleNamespace(),
        ws_manager=None,
        user_id=None,
        notification_repo=SimpleNamespace(),
    )

    sid = uuid.uuid4()

    async def _run():
        # 父 run 持锁
        lock = loop_mod._get_session_lock(sid)
        await lock.acquire()
        try:
            with patch.object(
                agent, "_run_locked", new=AsyncMock(return_value="nested done")
            ):
                # recorder 会尝试落库，无 DB 时静默失败（设计如此）
                return await asyncio.wait_for(
                    agent.run(sid, "task", _nested=True), timeout=3
                )
        finally:
            lock.release()

    assert asyncio.run(_run()) == "nested done"


# ═══════════ 4. DelegateTaskTool 集成 ═══════════

def test_delegate_task_routes_to_run_subagent():
    from backend.tools.builtins.agent_ops_tools import DelegateTaskTool

    agent = _fake_agent()
    repo = SimpleNamespace()
    repo.list_enabled = AsyncMock(return_value=[agent])

    recorder = SimpleNamespace(run_id=uuid.uuid4())
    captured: dict = {}

    async def _fake_runner(**kwargs):
        captured.update(kwargs)
        return "[delegate_task -> Coder]\nok"

    async def _run():
        with patch(
            "backend.repositories.sub_agent_repo.AsyncSubAgentRepository",
            return_value=repo,
        ), patch(
            "backend.agent.subagent_runner.run_subagent",
            new=_fake_runner,
        ):
            tool = DelegateTaskTool()
            return await tool.execute(
                action="run",
                goal="修 bug",
                context="上下文X",
                _session_id=str(uuid.uuid4()),
                _subagent_depth=0,
                _run_recorder=recorder,
            )

    out = asyncio.run(_run())
    assert out == "[delegate_task -> Coder]\nok"
    assert captured["goal"] == "修 bug"
    assert captured["context"] == "上下文X"
    assert captured["parent_run_id"] == recorder.run_id
    assert captured["depth"] == 0
    assert captured["sub_agent"] is agent
