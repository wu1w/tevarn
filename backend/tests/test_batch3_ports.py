"""Batch3: ports, loop base, SQLAlchemy message store adapter."""
from __future__ import annotations

from uuid import uuid4

import pytest

from backend.agent.loop_base import AgentLoopBase
from backend.integrations.registry_tool_executor import RegistryToolExecutor
from backend.integrations.sqlalchemy_message_store import SqlAlchemyMessageStore
from backend.interfaces.ports import MessageStorePort, ToolExecutorPort


class _FakeRepo:
    def __init__(self) -> None:
        self.saved: list[tuple] = []
        self.history: list[dict] = []

    async def save_message(self, session_id, role, content, tool_calls=None, token_count=None):
        row = {
            "session_id": session_id,
            "role": role,
            "content": content,
            "tool_calls": tool_calls,
            "token_count": token_count,
        }
        self.saved.append(row)
        self.history.append(row)
        return row

    async def get_history_by_session(self, session_id, limit=100, offset=0):
        return list(self.history)[offset : offset + limit]


@pytest.mark.asyncio
async def test_sqlalchemy_message_store_adapter():
    repo = _FakeRepo()
    store = SqlAlchemyMessageStore(repo)
    assert isinstance(store, MessageStorePort)
    sid = uuid4()
    await store.save_message(sid, "user", "hi")
    hist = await store.get_history(sid)
    assert len(hist) == 1 and hist[0]["content"] == "hi"


def test_registry_tool_executor_is_port():
    ex = RegistryToolExecutor()
    assert isinstance(ex, ToolExecutorPort)
    schemas = ex.list_schemas(None)
    assert isinstance(schemas, list)


def test_agent_loop_base_stop_and_ports():
    repo = _FakeRepo()
    store = SqlAlchemyMessageStore(repo)
    base = AgentLoopBase(message_store=store, agent_name="t")
    assert base.should_stop is False
    base.stop()
    assert base.should_stop is True
    base.reset_stop()
    assert base.should_stop is False


@pytest.mark.asyncio
async def test_loop_base_store_helpers():
    repo = _FakeRepo()
    store = SqlAlchemyMessageStore(repo)
    base = AgentLoopBase(message_store=store)
    sid = uuid4()
    await base.store_save_message(sid, "assistant", "ok")
    hist = await base.store_get_history(sid)
    assert hist[0]["role"] == "assistant"


def test_nexus_inherits_base():
    from backend.agent.loop import NexusAgentLoop
    from backend.agent.loop_base import AgentLoopBase

    assert issubclass(NexusAgentLoop, AgentLoopBase)


def test_takton_code_compat_import():
    # monorepo: backend available
    from takton_code.compat.backend_core import HAS_BACKEND, DoomLoopGuard, PermissionGate

    assert HAS_BACKEND is True
    g = DoomLoopGuard(threshold=2)
    assert g.record("t", {}) is False
    assert g.record("t", {}) is True
    pg = PermissionGate(profile="plan", mode="plan")
    assert pg.check("edit", {"path": "a.py"}) == "deny"
