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
    # monorepo: backend available（takton-code 独立 repo，无 checkout 时 skip）
    pytest.importorskip("takton_code.compat.backend_core", reason="takton-code repo not checked out")
    from takton_code.compat.backend_core import (
        HAS_BACKEND,
        DoomLoopGuard,
        PermissionGate,
    )

    assert HAS_BACKEND is True
    g = DoomLoopGuard(threshold=2)
    assert g.record("t", {}) is False
    assert g.record("t", {}) is True
    pg = PermissionGate(profile="plan", mode="plan")
    assert pg.check("edit", {"path": "a.py"}) == "deny"


@pytest.mark.asyncio
async def test_nexus_port_helpers_save_and_history():
    """NexusAgentLoop._save_message / _load_history go through message_store."""
    from backend.agent.loop import NexusAgentLoop

    repo = _FakeRepo()
    loop = NexusAgentLoop.__new__(NexusAgentLoop)
    from backend.integrations.sqlalchemy_message_store import SqlAlchemyMessageStore

    loop.message_repo = repo
    loop.message_store = SqlAlchemyMessageStore(repo)
    sid = uuid4()
    await loop._save_message(sid, "user", "hello-port")
    hist = await loop._load_history(sid)
    assert hist and hist[0]["content"] == "hello-port"


@pytest.mark.asyncio
async def test_nexus_execute_registered_tool_uses_executor():
    from backend.agent.loop import NexusAgentLoop

    class FakeEx:
        async def execute(self, name, arguments):
            return f"ok:{name}:{arguments.get('x')}"

        def list_schemas(self, names=None):
            return []

    loop = NexusAgentLoop.__new__(NexusAgentLoop)
    loop.tool_executor = FakeEx()
    out = await loop._execute_registered_tool("demo", {"x": 1})
    assert out == "ok:demo:1"
