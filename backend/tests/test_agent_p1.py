"""P1: checkpoint / soft input / resume helpers."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from backend.agent.checkpoint import (
    CHECKPOINT_KEY,
    clear_checkpoint,
    load_checkpoint,
    save_checkpoint,
)
from backend.agent.goal_state import (
    apply_manage_goal,
    clear_goal,
    ensure_goal,
    get_goal,
)
from backend.agent.resume import build_resume_prompt
from backend.services.cron_scheduler import compute_next_run


def test_soft_input_config_defaults():
    from backend.core.config import Settings

    s = Settings(
        jwt_secret="x" * 32,
        api_key="y" * 32,
        settings_encryption_salt="z" * 16,
    )
    assert s.agent_large_input_soft_chars > 0
    assert s.agent_auto_continue_max_segments >= 1
    assert s.agent_checkpoint_every >= 1


def test_goal_resume_prompt_when_incomplete():
    sid = str(uuid.uuid4())
    clear_goal(sid)
    ensure_goal(sid, title="Long job", description="3h")
    apply_manage_goal(
        sid,
        action="set_todos",
        todos=[{"id": "t1", "content": "step1", "status": "pending"}],
    )
    # build_resume_prompt is async — use pytest asyncio in separate test


@pytest.mark.asyncio
async def test_build_resume_prompt_goal():
    sid = uuid.uuid4()
    clear_goal(sid)
    ensure_goal(sid, title="Long job", description="3h")
    apply_manage_goal(
        sid,
        action="set_todos",
        todos=[{"id": "t1", "content": "step1", "status": "pending"}],
    )
    prompt = await build_resume_prompt(sid)
    assert prompt is not None
    assert "续跑" in prompt or "Goal" in prompt
    assert "step1" in prompt


@pytest.mark.asyncio
async def test_build_resume_prompt_none_when_done():
    sid = uuid.uuid4()
    clear_goal(sid)
    ensure_goal(sid, title="Done", description="")
    apply_manage_goal(sid, action="complete", completion_summary="ok")
    prompt = await build_resume_prompt(sid)
    # complete clears actionable work; may still have goal complete → None
    # complete marks complete so nothing to resume
    assert prompt is None or "完成" in (prompt or "")


def test_compute_next_run_stable():
    now = datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
    a = compute_next_run("every 10m", now)
    b = compute_next_run("every 10m", now)
    assert a == b
