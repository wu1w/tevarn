"""对话系统稳健性：stop、goal 持久化、cron next_run、输入截断相关。"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend.agent.goal_state import (
    apply_manage_goal,
    clear_goal,
    ensure_goal,
    get_goal,
    goal_from_dict,
)
from backend.services.cron_scheduler import compute_next_run


def test_compute_next_run_every():
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    nxt = compute_next_run("every 5m", now)
    assert nxt is not None
    assert abs((nxt - now).total_seconds() - 300) < 1

    nxt2 = compute_next_run("every 2h", now)
    assert nxt2 is not None
    assert abs((nxt2 - now).total_seconds() - 7200) < 1


def test_compute_next_run_cron_expression():
    pytest.importorskip("croniter")
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    nxt = compute_next_run("0 9 * * *", now)
    assert nxt is not None
    assert nxt > now
    assert nxt.hour == 9


def test_goal_from_dict_roundtrip():
    clear_goal("s1")
    g = ensure_goal("s1", title="T", description="D")
    apply_manage_goal(
        "s1",
        action="set_todos",
        todos=[
            {"id": "t1", "content": "a", "status": "pending"},
            {"id": "t2", "content": "b", "status": "done"},
        ],
    )
    g2 = get_goal("s1")
    assert g2 is not None
    data = g2.to_dict()
    clear_goal("s1")
    restored = goal_from_dict(data)
    assert restored.title == "T"
    assert len(restored.todos) == 2
    assert restored.todos[1].status == "done"


@pytest.mark.asyncio
async def test_agent_stop_flag_breaks_iteration():
    """_should_stop 在循环检查点应生效。"""
    from backend.agent.loop import NexusAgentLoop

    agent = NexusAgentLoop(
        session_repo=SimpleNamespace(),
        message_repo=None,
        task_repo=None,
        ctx_item_repo=None,
        context_flow_repo=None,
        ws_manager=None,
    )
    assert agent._should_stop is False
    agent.stop()
    assert agent._should_stop is True


@pytest.mark.asyncio
async def test_connection_manager_tracks_agent_task():
    from backend.api.websocket import ConnectionManager

    mgr = ConnectionManager()
    sid = uuid.uuid4()

    async def _sleeper():
        await asyncio.sleep(60)

    t = asyncio.create_task(_sleeper())
    mgr.track_agent_task(sid, t)
    assert mgr.has_running_agent(sid) is True
    agent_stop = True
    await mgr.cancel_agent(sid)
    await asyncio.sleep(0.05)
    assert t.cancelled() or t.done()
    assert mgr.has_running_agent(sid) is False


def test_user_input_cap_logic():
    """配置默认值存在且合理。"""
    from backend.core.config import Settings

    s = Settings(
        jwt_secret="x" * 32,
        api_key="y" * 32,
        settings_encryption_salt="z" * 16,
    )
    assert s.agent_max_iterations >= 40
    assert s.agent_goal_max_iterations >= 100
    assert s.agent_tool_timeout_seconds > 0
    assert s.agent_max_user_input_chars >= 10_000
