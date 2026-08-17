"""loop 行为冻结测试（Phase 1 loop 拆分前置）

拆分 _run_locked 之前的 characterization 基线：
- 简单问答：LLM 流式 delta 拼成最终回复并持久化
- 工具流：tool_call → 工具执行 → tool 消息回灌 → 第二轮得到最终回复
- 停止信号：流中 _should_stop → 保留半截正文且不炸

这些测试必须先于拆分通过，拆分后也必须原样通过（行为不变）。
"""
import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

# ─────────── 测试装置 ───────────

def _chunk(delta=None, tool_call=None, finish=None):
    return SimpleNamespace(
        delta=delta,
        tool_call=tool_call,
        finish_reason=finish,
        reasoning_delta=None,
    )


def _tc(tc_id, name, arguments):
    return SimpleNamespace(id=tc_id, name=name, arguments=arguments)


class _ScriptedLLM:
    """按剧本逐轮产出 chunk 的假 LLM 服务"""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    async def chat(self, messages, tools=None, stream=True):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools})
        chunks = self.script.pop(0) if self.script else [_chunk(delta="(剧本耗尽)", finish="stop")]
        for ch in chunks:
            yield ch


def _make_loop(llm):
    """构造打满补丁的 NexusAgentLoop（不触 DB/WS/RAG）"""
    from backend.agent.loop import NexusAgentLoop

    sid_session = SimpleNamespace(id=uuid.uuid4())
    session_repo = SimpleNamespace(
        get_with_lock=AsyncMock(return_value=sid_session),
        get_config=AsyncMock(return_value={}),
        update_status=AsyncMock(),
    )
    loop = NexusAgentLoop(
        session_repo=session_repo,
        message_repo=SimpleNamespace(),
        task_repo=SimpleNamespace(),
        ctx_item_repo=SimpleNamespace(),
        context_flow_repo=None,
        ws_manager=None,
        user_id=None,
        notification_repo=SimpleNamespace(),
    )
    loop.max_iterations = 5
    # 持久化/推送全部 mock
    loop._persist_user_input = AsyncMock()
    loop._persist_final_response = AsyncMock()
    loop._save_message = AsyncMock()
    loop._load_history = AsyncMock(return_value=[])
    loop._push_status = AsyncMock()
    loop._push_stream = AsyncMock()
    loop._emit_progress = AsyncMock()
    loop._record_flow = AsyncMock()
    loop._check_auto_optimize = AsyncMock()
    loop._push_goal_update = AsyncMock()
    loop._persist_tool_start = AsyncMock(return_value=None)
    loop._persist_tool_failure = AsyncMock()
    loop._push_task_update = AsyncMock()
    loop._push_tool_event = AsyncMock()
    # context 组装：最小 system+user
    loop.context_manager = SimpleNamespace(
        build_messages=AsyncMock(side_effect=lambda session_id, user_input, history, **kw: (
            [
                {"role": "system", "content": "你是助手"},
                *history,
                {"role": "user", "content": user_input},
            ],
            [],
            0,
        ))
    )
    # LLM 服务
    patcher = patch(
        "backend.services.llm.LLMServiceFactory.get_service_for_snapshot",
        return_value=llm,
    )
    patcher.start()
    return loop, patcher


async def _run_loop(loop, sid, text="你好", mode="default"):
    return await loop.run(sid, text, attachments=None, mode=mode)


# ─────────── 冻结用例 ───────────

def test_freeze_simple_chat():
    """简单问答：delta 拼接 → 最终回复 → 持久化"""
    llm = _ScriptedLLM([
        [_chunk(delta="你好"), _chunk(delta="，世界", finish="stop")],
    ])
    loop, patcher = _make_loop(llm)
    try:
        sid = uuid.uuid4()
        out = asyncio.run(_run_loop(loop, sid))
    finally:
        patcher.stop()

    assert out == "你好，世界"
    assert len(llm.calls) == 1
    # 最终回复持久化
    final_calls = list(loop._persist_final_response.call_args_list)
    assert final_calls and final_calls[-1].args[1] == "你好，世界"
    # 用户输入先持久化
    assert loop._persist_user_input.called
    # 无工具调用时 LLM 收到 tools=None 或非空 schema 均可，但必须只调一次
    assert loop._push_status.called


def test_freeze_tool_call_flow():
    """工具流：tool_call → 执行 → tool 消息回灌 → 二轮回复"""
    llm = _ScriptedLLM([
        [_chunk(tool_call=_tc("tc1", "file_read", {"path": "a.py"}), finish="tool_calls")],
        [_chunk(delta="文件内容是 X", finish="stop")],
    ])
    loop, patcher = _make_loop(llm)
    loop._execute_registered_tool = AsyncMock(return_value="TOOL_OUTPUT_X")
    # 工具路径先经 ToolRegistry.get；注入一个假工具让它走到 _execute_registered_tool
    registry_patcher = patch(
        "backend.tools.registry.ToolRegistry.get",
        return_value=SimpleNamespace(parameters={"type": "object", "properties": {}}),
    )
    registry_patcher.start()
    try:
        sid = uuid.uuid4()
        out = asyncio.run(_run_loop(loop, sid, text="读一下 a.py"))
    finally:
        registry_patcher.stop()
        patcher.stop()

    assert out == "文件内容是 X"
    assert len(llm.calls) == 2
    # 工具被执行一次
    loop._execute_registered_tool.assert_called_once()
    assert loop._execute_registered_tool.call_args.args[0] == "file_read"
    # 第二轮 LLM 的 messages 里含 tool 角色消息，内容=工具结果
    second_msgs = llm.calls[1]["messages"]
    tool_msgs = [m for m in second_msgs if m.get("role") == "tool"]
    assert tool_msgs, "第二轮必须回灌 tool 消息"
    assert any("TOOL_OUTPUT_X" in str(m.get("content")) for m in tool_msgs)
    assert any(m.get("tool_call_id") == "tc1" for m in tool_msgs)


def test_freeze_stop_signal():
    """流中停止：_should_stop 置位 → 保留已流出正文，不插入内部 Stopped 标记"""
    loop_holder: dict = {}

    class _StopLLM:
        async def chat(self, messages, tools=None, stream=True):
            loop_holder["loop"]._should_stop = True
            yield _chunk(delta="半截", finish="stop")

    llm = _StopLLM()
    loop, patcher = _make_loop(llm)
    loop_holder["loop"] = loop
    try:
        sid = uuid.uuid4()
        out = asyncio.run(_run_loop(loop, sid))
    finally:
        patcher.stop()
        loop._should_stop = False

    assert "半截" in (out or "")
    assert "[Stopped]" not in (out or "")
    assert getattr(loop, "last_exit_reason", "") == "stopped_by_user"


def test_freeze_llm_error_surfaces():
    """LLM 报错：不静默假装成功（H0 红线），最终文本暴露错误"""
    class _ErrLLM:
        async def chat(self, messages, tools=None, stream=True):
            raise RuntimeError("provider exploded")
            yield  # pragma: no cover

    loop, patcher = _make_loop(_ErrLLM())
    try:
        sid = uuid.uuid4()
        out = asyncio.run(_run_loop(loop, sid))
    finally:
        patcher.stop()

    # 冻结口径：必须有可见失败文案，不允许空串/假装成功；不把异常栈泄到聊天
    assert isinstance(out, str) and out.strip()
    assert "exploded" not in out
    assert "出错" in out or "失败" in out or "再试" in out
