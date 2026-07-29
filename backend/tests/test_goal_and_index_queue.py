"""Tests for real audit fixes: goal write-through + identity index queue."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.agent.goal_state import (
    apply_manage_goal,
    clear_goal,
    ensure_goal,
    get_goal,
    goal_from_dict,
    put_goal_cache,
)
from backend.services.rag import identity_index_queue as q


@pytest.fixture(autouse=True)
def _isolate_queue(tmp_path, monkeypatch):
    """Point TAKTON_HOME at tmp so queue tests don't touch user data."""
    monkeypatch.setenv("TAKTON_HOME", str(tmp_path))
    # reset path cache via new home
    yield
    clear_goal("test-goal-sess")


def test_goal_cache_overwrite_from_dict():
    clear_goal("s1")
    g = ensure_goal("s1", title="A")
    g.status = "active"
    assert get_goal("s1") is not None
    # simulate DB load with newer state
    newer = goal_from_dict(
        {
            "session_id": "s1",
            "title": "B",
            "status": "completed",
            "todos": [{"id": "t1", "content": "done item", "status": "done"}],
        }
    )
    put_goal_cache(newer)
    got = get_goal("s1")
    assert got is not None
    assert got.title == "B"
    assert got.status == "completed"
    assert len(got.todos) == 1


def test_apply_manage_goal_mutates_status():
    clear_goal("s2")
    r = apply_manage_goal("s2", action="create", title="Ship", description="x")
    assert r["ok"] is True
    r2 = apply_manage_goal(
        "s2",
        action="set_todos",
        todos=[{"id": "t1", "content": "a", "status": "pending"}],
    )
    assert r2["ok"]
    r3 = apply_manage_goal("s2", action="update_todo", todo_id="t1", status="done")
    assert r3["ok"]
    g = get_goal("s2")
    assert g is not None
    assert g.todos[0].status == "done"


def test_index_queue_enqueue_dedupe_and_persist(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKTON_HOME", str(tmp_path))
    q.enqueue(
        entry_id="e1",
        identity_id="i1",
        kind="experience",
        content="hello",
        version=1,
    )
    q.enqueue(
        entry_id="e1",
        identity_id="i1",
        kind="experience",
        content="hello v2",
        version=2,
    )
    assert q.pending_count() == 1
    p = Path(tmp_path) / "rag" / "identity_index_pending.jsonl"
    assert p.is_file()
    lines = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(lines) == 1
    assert lines[0]["content"] == "hello v2"
    assert lines[0]["version"] == 2


@pytest.mark.asyncio
async def test_index_queue_flush_without_vector_rag(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKTON_HOME", str(tmp_path))
    q.enqueue(
        entry_id="e2",
        identity_id="i2",
        kind="experience",
        content="x",
    )
    # use_vector_rag false → skip, keep queue
    monkeypatch.setattr(
        "backend.services.rag.capability.use_vector_rag",
        lambda: False,
        raising=False,
    )
    # Prefer patching the import site inside flush
    import backend.services.rag.identity_index_queue as mod

    class _Cap:
        @staticmethod
        def use_vector_rag():
            return False

    monkeypatch.setattr(
        mod,
        "flush_pending",
        mod.flush_pending,
    )
    # Call real flush — it imports use_vector_rag internally
    from backend.services.rag import capability as cap

    monkeypatch.setattr(cap, "use_vector_rag", lambda: False)
    stats = await mod.flush_pending(limit=5)
    assert stats.get("skipped") == 1 or stats.get("left", 0) >= 1
