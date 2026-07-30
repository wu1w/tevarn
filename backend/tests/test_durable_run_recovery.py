"""Phase 2.3：checkpoint 权威落 Run + 启动恢复（模拟 kill-9）。"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_checkpoint_dual_write_to_run_column():
    from backend.agent.checkpoint import load_checkpoint, save_checkpoint
    from backend.agent.run_lifecycle import build_create_payload
    from backend.repositories.agent_run_repo import AsyncAgentRunRepository
    from backend.repositories.session_repo import AsyncSessionRepository
    from backend.repositories.user_repo import AsyncUserRepository

    users = AsyncUserRepository()
    uname = f"cp_{uuid.uuid4().hex[:8]}"
    user = await users.create(
        {
            "email": f"{uname}@example.com",
            "username": uname,
            "hashed_password": "x",
        }
    )
    sessions = AsyncSessionRepository()
    session = await sessions.create(
        {"user_id": user.id, "config": {"identity": "t", "skills": []}}
    )
    repo = AsyncAgentRunRepository()
    run = await repo.create_run(
        build_create_payload(
            session_id=session.id,
            user_id=user.id,
            mode="default",
            origin="chat",
            input_summary="long job",
        )
    )

    await save_checkpoint(
        session.id,
        segment=2,
        iteration=21,
        mode="goal",
        note="mid-run",
        run_id=str(run.id),
        extra={"steps_done": ["a", "b"]},
    )

    # Run 列权威
    row = await repo.get_run(run.id)
    assert row is not None
    assert row.checkpoint is not None
    assert row.checkpoint["iteration"] == 21
    assert row.checkpoint["segment"] == 2

    # load 优先 Run 列
    cp = await load_checkpoint(session.id, run_id=run.id)
    assert cp is not None
    assert cp["iteration"] == 21
    assert cp["extra"]["steps_done"] == ["a", "b"]


@pytest.mark.asyncio
async def test_recover_marks_interrupted_and_auto_resumes_inbox():
    """模拟 kill-9：executing 行被标 interrupted；inbox 路径触发 resume。"""
    from backend.agent.run_lifecycle import build_create_payload
    from backend.agent.run_recovery import recover_stale_runs
    from backend.agent.run_state import RunStatus
    from backend.repositories.agent_run_repo import AsyncAgentRunRepository
    from backend.repositories.session_repo import AsyncSessionRepository
    from backend.repositories.user_repo import AsyncUserRepository

    users = AsyncUserRepository()
    uname = f"rc_{uuid.uuid4().hex[:8]}"
    user = await users.create(
        {
            "email": f"{uname}@example.com",
            "username": uname,
            "hashed_password": "x",
        }
    )
    sessions = AsyncSessionRepository()
    session = await sessions.create(
        {"user_id": user.id, "config": {"identity": "t", "skills": []}}
    )
    repo = AsyncAgentRunRepository()

    inbox_run = await repo.create_run(
        {
            **build_create_payload(
                session_id=session.id,
                user_id=user.id,
                mode="workforce",
                origin="inbox",
                input_summary="job",
            ),
            "status": RunStatus.EXECUTING.value,
            "started_at": datetime.now(timezone.utc),
        }
    )
    chat_run = await repo.create_run(
        {
            **build_create_payload(
                session_id=session.id,
                user_id=user.id,
                mode="default",
                origin="chat",
                input_summary="chat",
            ),
            "status": RunStatus.EXECUTING.value,
            "started_at": datetime.now(timezone.utc),
        }
    )

    resume_mock = AsyncMock(return_value="[resume] ok continued")
    with patch("backend.agent.resume.resume_session_agent", resume_mock):
        summary = await recover_stale_runs(auto_resume=True)

    assert summary["marked_interrupted"] >= 2
    assert summary["resumed"] >= 1
    assert summary["skipped_chat"] >= 1

    # inbox 被 resume（mock 调用）
    assert resume_mock.await_count >= 1

    chat_row = await repo.get_run(chat_run.id)
    assert chat_row is not None
    # chat 保持 interrupted（不自动 resume）
    assert chat_row.status == RunStatus.INTERRUPTED.value


@pytest.mark.asyncio
async def test_recover_respects_auto_recover_flag():
    from backend.agent.run_lifecycle import build_create_payload
    from backend.agent.run_recovery import recover_stale_runs
    from backend.agent.run_state import RunStatus
    from backend.repositories.agent_run_repo import AsyncAgentRunRepository
    from backend.repositories.session_repo import AsyncSessionRepository
    from backend.repositories.user_repo import AsyncUserRepository

    users = AsyncUserRepository()
    uname = f"rf_{uuid.uuid4().hex[:8]}"
    user = await users.create(
        {
            "email": f"{uname}@example.com",
            "username": uname,
            "hashed_password": "x",
        }
    )
    sessions = AsyncSessionRepository()
    session = await sessions.create(
        {"user_id": user.id, "config": {"identity": "t", "skills": []}}
    )
    repo = AsyncAgentRunRepository()
    await repo.create_run(
        {
            **build_create_payload(
                session_id=session.id,
                origin="inbox",
                mode="workforce",
                input_summary="x",
            ),
            "status": RunStatus.PLANNING.value,
        }
    )

    resume_mock = AsyncMock(return_value="ok")
    with patch("backend.agent.resume.resume_session_agent", resume_mock):
        summary = await recover_stale_runs(auto_resume=False)

    assert summary["marked_interrupted"] >= 1
    assert resume_mock.await_count == 0
    assert summary["auto_resume_enabled"] is False


def test_interrupted_sm_allows_resume():
    from backend.agent.run_state import RunStatus, can_transition, validate_transition

    assert can_transition(RunStatus.EXECUTING, RunStatus.INTERRUPTED)
    assert can_transition(RunStatus.INTERRUPTED, RunStatus.EXECUTING)
    assert validate_transition(RunStatus.INTERRUPTED, RunStatus.EXECUTING) == RunStatus.EXECUTING
