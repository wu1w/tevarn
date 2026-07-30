"""Phase 2.1 Run 统一：origin 推断 / lifecycle / repo 列 / recorder。"""
from __future__ import annotations

import uuid

import pytest


# ── lifecycle 纯函数 ──────────────────────────────────────────


def test_infer_origin_from_mode_and_meta():
    from backend.agent.run_lifecycle import infer_origin

    assert infer_origin(mode="default") == "chat"
    assert infer_origin(mode="workforce") == "inbox"
    assert infer_origin(mode="subagent") == "subagent"
    assert infer_origin(mode="headless") == "headless"
    assert infer_origin(meta={"inbox_item_id": "x"}) == "inbox"
    assert infer_origin(meta={"cluster_run_id": "c1"}) == "cluster"
    assert infer_origin(meta={"source": "cron"}) == "cron"
    assert infer_origin(explicit="cron", mode="default") == "cron"
    assert infer_origin(parent_run_id=uuid.uuid4()) == "subagent"


def test_public_status_mapping():
    from backend.agent.run_lifecycle import public_status

    assert public_status("created") == "pending"
    assert public_status("executing") == "running"
    assert public_status("planning") == "running"
    assert public_status("waiting") == "waiting_approval"
    assert public_status("done") == "done"
    assert public_status("failed") == "failed"


def test_build_create_payload_sets_columns():
    from backend.agent.run_lifecycle import build_create_payload

    sid = uuid.uuid4()
    parent = uuid.uuid4()
    ident = uuid.uuid4()
    p = build_create_payload(
        session_id=sid,
        mode="subagent",
        parent_run_id=parent,
        identity_id=ident,
        token_limit=50_000,
        input_summary="hi",
    )
    assert p["origin"] == "subagent"
    assert p["parent_run_id"] == parent
    assert p["identity_id"] == ident
    assert p["token_limit"] == 50_000
    assert p["meta"]["origin"] == "subagent"
    assert p["meta"]["parent_run_id"] == str(parent)


# ── repo + recorder ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_repo_create_and_list_by_origin():
    """真实 sqlite：create 带 origin，list_recent(origin=) 过滤。"""
    from backend.models.agent_run import AgentRun  # noqa: F401 — 注册表
    from backend.repositories.agent_run_repo import AsyncAgentRunRepository
    from backend.repositories.session_repo import AsyncSessionRepository
    from backend.repositories.user_repo import AsyncUserRepository

    users = AsyncUserRepository()
    uname = f"run_u_{uuid.uuid4().hex[:8]}"
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
    from backend.agent.run_lifecycle import build_create_payload

    r1 = await repo.create_run(
        build_create_payload(
            session_id=session.id,
            user_id=user.id,
            mode="workforce",
            input_summary="job-a",
        )
    )
    r2 = await repo.create_run(
        build_create_payload(
            session_id=session.id,
            user_id=user.id,
            mode="default",
            input_summary="chat-b",
        )
    )
    assert r1.origin == "inbox"
    assert r2.origin == "chat"

    inbox_only = await repo.list_recent(limit=20, origin="inbox")
    ids = {x.id for x in inbox_only}
    assert r1.id in ids
    assert r2.id not in ids


@pytest.mark.asyncio
async def test_recorder_writes_origin():
    from backend.agent.run_recorder import RunRecorder
    from backend.repositories.agent_run_repo import AsyncAgentRunRepository
    from backend.repositories.session_repo import AsyncSessionRepository
    from backend.repositories.user_repo import AsyncUserRepository

    users = AsyncUserRepository()
    uname = f"run_r_{uuid.uuid4().hex[:8]}"
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

    rec = RunRecorder(
        session.id,
        user_id=user.id,
        mode="workforce",
        meta={"inbox_item_id": str(uuid.uuid4())},
        token_limit=12_000,
    )
    rid = await rec.start("do work")
    assert rid is not None
    row = await AsyncAgentRunRepository().get_run(rid)
    assert row is not None
    assert row.origin == "inbox"
    assert row.token_limit == 12_000
    assert (row.meta or {}).get("inbox_item_id")
