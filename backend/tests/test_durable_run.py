"""Phase 0.5.2 Durable Run/Step + SM + EventBus 测试

覆盖：
- run_state 状态机（合法路径 / 非法迁移 / 同态 no-op）
- EventBus（模式匹配 / 退订 / 订阅者异常隔离）
- agent_run_repo（真实临时 sqlite 的 CRUD round-trip）
- RunRecorder（全流程 / 非法迁移降级 / 失败静默 / 事件顺序）
- checkpoint 带 run_id
"""
import asyncio
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

# ═══════════ 1. 状态机 ═══════════

def test_sm_happy_path():
    from backend.agent.run_state import RunStatus as RS
    from backend.agent.run_state import validate_transition

    cur = RS.CREATED
    for nxt in (RS.PLANNING, RS.EXECUTING, RS.VERIFYING, RS.DONE):
        cur = validate_transition(cur, nxt)
    assert cur == RS.DONE


def test_sm_illegal_transition_raises():
    from backend.agent.run_state import (
        IllegalTransitionError,
        validate_transition,
    )
    from backend.agent.run_state import (
        RunStatus as RS,
    )

    with pytest.raises(IllegalTransitionError):
        validate_transition(RS.EXECUTING, RS.CREATED)
    with pytest.raises(IllegalTransitionError):
        validate_transition(RS.DONE, RS.EXECUTING)
    with pytest.raises(IllegalTransitionError):
        validate_transition(RS.FAILED, RS.PLANNING)


def test_sm_accepts_str_and_same_state_noop():
    from backend.agent.run_state import RunStatus as RS
    from backend.agent.run_state import can_transition

    assert can_transition("created", "planning")
    assert can_transition(RS.EXECUTING, RS.EXECUTING)  # 同态 no-op
    assert not can_transition("cancelled", "executing")


def test_sm_terminal_absorbs_from_any_non_terminal():
    from backend.agent.run_state import RunStatus as RS
    from backend.agent.run_state import can_transition

    for src in (RS.CREATED, RS.PLANNING, RS.EXECUTING, RS.WAITING, RS.VERIFYING):
        assert can_transition(src, RS.DONE)
        assert can_transition(src, RS.FAILED)
        assert can_transition(src, RS.CANCELLED)


# ═══════════ 2. EventBus ═══════════

def test_bus_pattern_matching():
    from backend.core.event_bus import EventBus

    async def _run():
        bus = EventBus()
        got: list[tuple[str, dict]] = []

        async def h(topic, payload):
            got.append((topic, payload))

        bus.subscribe("run.*", h)
        await bus.publish("run.created", {"run_id": "r1"})
        await bus.publish("step.started", {"run_id": "r1"})
        assert [t for t, _ in got] == ["run.created"]

    asyncio.run(_run())


def test_bus_wildcard_and_unsub():
    from backend.core.event_bus import EventBus

    async def _run():
        bus = EventBus()
        got: list[str] = []

        async def h(topic, payload):
            got.append(topic)

        unsub = bus.subscribe("*", h)
        await bus.publish("tool.completed", {})
        unsub()
        await bus.publish("tool.failed", {})
        assert got == ["tool.completed"]
        assert bus.subscriber_count() == 0

    asyncio.run(_run())


def test_bus_subscriber_error_isolated():
    from backend.core.event_bus import EventBus

    async def _run():
        bus = EventBus()
        got: list[str] = []

        async def bad(topic, payload):
            raise RuntimeError("boom")

        async def good(topic, payload):
            got.append(topic)

        bus.subscribe("run.*", bad)
        bus.subscribe("run.*", good)
        await bus.publish("run.created", {})  # 不抛出
        assert got == ["run.created"]

    asyncio.run(_run())


# ═══════════ 3. Repo（真实临时 sqlite） ═══════════

def test_repo_crud_roundtrip(tmp_path):
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    from backend.models.agent_run import AgentRun, RunStep  # noqa: F401 注册表
    from backend.models.base import Base
    from backend.repositories.agent_run_repo import AsyncAgentRunRepository

    db_file = tmp_path / "runs.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", future=True)
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _run():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session = SessionLocal()
        repo = AsyncAgentRunRepository(session)  # 注入 session：只 flush 不 commit
        sid = uuid.uuid4()

        run = await repo.create_run({
            "session_id": sid,
            "status": "created",
            "mode": "default",
            "input_summary": "写个脚本",
        })
        assert run.id is not None

        got = await repo.get_run(run.id)
        assert got is not None and got.status == "created"

        await repo.update_run(run.id, {"status": "done", "total_tool_calls": 3})
        got = await repo.get_run(run.id)
        assert got.status == "done" and got.total_tool_calls == 3

        await repo.add_step({"run_id": run.id, "seq": 1, "kind": "phase", "name": "created -> planning"})
        await repo.add_step({"run_id": run.id, "seq": 2, "kind": "tool", "name": "file_write", "duration_ms": 12.5})
        steps = await repo.list_steps(run.id)
        assert [s.seq for s in steps] == [1, 2]
        assert steps[1].name == "file_write"

        runs = await repo.list_runs(sid)
        assert len(runs) == 1 and runs[0].id == run.id

        await session.close()

    asyncio.run(_run())


# ═══════════ 4. RunRecorder ═══════════

def _mock_repo():
    repo = SimpleNamespace()
    repo.create_run = AsyncMock(side_effect=lambda data: SimpleNamespace(id=uuid.uuid4()))
    repo.update_run = AsyncMock(return_value=None)
    repo.add_step = AsyncMock(side_effect=lambda data: SimpleNamespace(id=uuid.uuid4(), **data))
    repo.list_steps = AsyncMock(return_value=[])
    return repo


def test_recorder_full_flow():
    from backend.agent.run_recorder import RunRecorder
    from backend.core.event_bus import event_bus

    repo = _mock_repo()
    events: list[str] = []

    async def capture(topic, payload):
        events.append(topic)

    async def _run():
        with patch(
            "backend.repositories.agent_run_repo.AsyncAgentRunRepository",
            return_value=repo,
        ):
            unsub = event_bus.subscribe("*", capture)
            try:
                sid = uuid.uuid4()
                rc = RunRecorder(sid, mode="default")
                rid = await rc.start(input_summary="帮我写文件")
                assert rid is not None

                assert await rc.transition("planning")
                assert await rc.transition("executing")
                await rc.tool_step("file_write", status="completed", duration_ms=5.0)
                await rc.tool_step("shell", status="failed", result_summary="[Error] 127")
                assert await rc.transition("verifying")
                await rc.finish_ok(final_summary="写完了")

                # 终态落库
                final = repo.update_run.call_args_list[-1]
                data = final.args[1]
                assert data["status"] == "done"
                assert data["total_tool_calls"] == 2
                assert "ended_at" in data

                # 步骤流水：3 次 phase + 2 次 tool
                kinds = [c.args[0]["kind"] for c in repo.add_step.call_args_list]
                assert kinds == ["phase", "phase", "tool", "tool", "phase"]
            finally:
                unsub()

    asyncio.run(_run())
    # 事件顺序
    assert events[0] == "run.created"
    assert "run.status_changed" in events
    assert "tool.completed" in events and "tool.failed" in events
    assert events[-1] == "run.completed"


def test_recorder_illegal_transition_skipped():
    from backend.agent.run_recorder import RunRecorder

    repo = _mock_repo()

    async def _run():
        with patch(
            "backend.repositories.agent_run_repo.AsyncAgentRunRepository",
            return_value=repo,
        ):
            rc = RunRecorder(uuid.uuid4())
            await rc.start()
            assert await rc.transition("executing")
            # executing -> created 非法：返回 False 且不写库
            assert await rc.transition("created") is False
            statuses = [c.args[1].get("status") for c in repo.update_run.call_args_list]
            assert "created" not in statuses

    asyncio.run(_run())


def test_recorder_failure_safe_when_db_down():
    from backend.agent.run_recorder import RunRecorder

    repo = _mock_repo()
    repo.create_run = AsyncMock(side_effect=RuntimeError("db down"))

    async def _run():
        with patch(
            "backend.repositories.agent_run_repo.AsyncAgentRunRepository",
            return_value=repo,
        ):
            rc = RunRecorder(uuid.uuid4())
            assert await rc.start() is None  # 失败返回 None
            # 后续全部 no-op，绝不 raise 进主循环
            assert await rc.transition("planning") is False
            await rc.tool_step("shell")
            await rc.finish_ok("done")

    asyncio.run(_run())


def test_recorder_finish_idempotent():
    from backend.agent.run_recorder import RunRecorder

    repo = _mock_repo()

    async def _run():
        with patch(
            "backend.repositories.agent_run_repo.AsyncAgentRunRepository",
            return_value=repo,
        ):
            rc = RunRecorder(uuid.uuid4())
            await rc.start()
            await rc.finish_ok("a")
            await rc.finish_fail("b")  # 已终态，忽略
            statuses = [c.args[1]["status"] for c in repo.update_run.call_args_list]
            assert statuses == ["done"]

    asyncio.run(_run())


@pytest.mark.asyncio
async def test_token_used_updated_when_stream_reports_usage():
    """Usage kept after finish_reason must land on agent_runs.token_used."""
    from backend.agent.run_recorder import RunRecorder
    from backend.services.llm.openai_compatible import OpenAIStreamAccumulator

    repo = _mock_repo()
    with patch(
        "backend.repositories.agent_run_repo.AsyncAgentRunRepository",
        return_value=repo,
    ):
        rec = RunRecorder(uuid.uuid4())
        await rec.start("task")
        acc = OpenAIStreamAccumulator(
            uuid.uuid4(),
            normalize_usage=lambda u: {
                "prompt_tokens": int(u.get("prompt_tokens") or 0),
                "completion_tokens": int(u.get("completion_tokens") or 0),
                "total_tokens": int(u.get("total_tokens") or 0),
            },
        )
        acc.consume_data_line(json.dumps({
            "choices": [{"delta": {"content": "hi"}, "finish_reason": "stop"}],
        }))
        acc.consume_data_line(json.dumps({
            "choices": [],
            "usage": {
                "prompt_tokens": 1200,
                "completion_tokens": 80,
                "total_tokens": 1280,
            },
        }))
        done, chunks = acc.consume_data_line("[DONE]")
        assert done is True
        finish = next(c for c in chunks if c.finish_reason)
        rec.note_llm_round_usage(finish.usage)
        rec.note_llm_round_usage({
            "prompt_tokens": 400,
            "completion_tokens": 20,
            "total_tokens": 420,
        })
        rec.set_token_used(0)
        await rec.finish_ok("done")

    final = repo.update_run.call_args_list[-1].args[1]
    assert final["token_used"] == 1280 + 420
    assert final["meta"]["token_used_source"] == "provider"


@pytest.mark.asyncio
async def test_token_used_omitted_is_explicit_not_silent_zero():
    from backend.agent.run_recorder import RunRecorder

    repo = _mock_repo()
    with patch(
        "backend.repositories.agent_run_repo.AsyncAgentRunRepository",
        return_value=repo,
    ):
        rec = RunRecorder(uuid.uuid4())
        await rec.start("task")
        rec.note_llm_round_usage(None)
        rec.note_llm_round_usage({})
        rec.set_token_used(0)
        await rec.finish_ok("done")

    final = repo.update_run.call_args_list[-1].args[1]
    assert final["token_used"] == 0
    assert final["meta"]["token_used_source"] == "omitted"


@pytest.mark.asyncio
async def test_token_used_partial_when_some_rounds_lack_usage():
    from backend.agent.run_recorder import RunRecorder

    repo = _mock_repo()
    with patch(
        "backend.repositories.agent_run_repo.AsyncAgentRunRepository",
        return_value=repo,
    ):
        rec = RunRecorder(uuid.uuid4())
        await rec.start("task")
        rec.note_llm_round_usage({"prompt_tokens": 10, "completion_tokens": 2})
        rec.note_llm_round_usage(None)
        await rec.finish_ok("done")

    final = repo.update_run.call_args_list[-1].args[1]
    assert final["token_used"] == 12
    assert final["meta"]["token_used_source"] == "partial"


@pytest.mark.asyncio
async def test_llm_round_notes_usage_onto_recorder():
    """llm_round must call note_llm_round_usage — not only kernel charge."""
    import inspect

    from backend.agent.phases import llm_round as mod

    src = inspect.getsource(mod._run_llm_round_body)
    assert "note_llm_round_usage" in src
    assert "stream_usage" in src


# ═══════════ 5. checkpoint 带 run_id ═══════════

def test_checkpoint_carries_run_id():
    from backend.agent.checkpoint import save_checkpoint

    cfg_store: dict = {}

    repo = SimpleNamespace()
    repo.get_config = AsyncMock(return_value={})
    # checkpoint 已改走键级合并 merge_config_keys（updates dict 直接并入 cfg_store）
    repo.merge_config_keys = AsyncMock(
        side_effect=lambda sid, updates=None, *, remove=None: cfg_store.update(updates or {})
    )

    async def _run():
        with patch(
            "backend.repositories.session_repo.AsyncSessionRepository",
            return_value=repo,
        ):
            await save_checkpoint(
                uuid.uuid4(),
                segment=2, iteration=7, mode="goal",
                note="budget_exhausted",
                run_id="run-abc-123",
            )
            assert cfg_store["_agent_checkpoint"]["run_id"] == "run-abc-123"

            cfg_store.clear()
            await save_checkpoint(uuid.uuid4(), segment=1, iteration=1, mode="default")
            assert "run_id" not in cfg_store["_agent_checkpoint"]

    asyncio.run(_run())
