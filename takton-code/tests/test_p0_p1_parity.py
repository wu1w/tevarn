"""P0/P1: export, import, todos, agents, stats."""

from __future__ import annotations

from pathlib import Path

import pytest

from takton_code.agent.agents_fs import get_agent, load_agents
from takton_code.session.export_fmt import load_export_file, to_jsonl, to_markdown, write_export
from takton_code.session.store import SessionStore


@pytest.mark.asyncio
async def test_todo_export_import_fork_stats(tmp_path: Path):
    db = tmp_path / "s.db"
    store = SessionStore(db)
    await store.open()
    sid = await store.create_session(project_root=str(tmp_path), title="t")
    await store.set_todos(
        sid,
        [
            {"content": "a", "status": "pending"},
            {"content": "b", "status": "done"},
        ],
    )
    await store.append_message(sid, "user", "hello")
    await store.append_message(sid, "assistant", "hi")
    data = await store.export_session(sid)
    assert len(data["todos"]) == 2
    md = to_markdown(data)
    assert "hello" in md
    jl = to_jsonl(data)
    assert "session_meta" in jl
    out = write_export(tmp_path, sid, data, fmt="md")
    assert out.suffix == ".md"
    outj = write_export(tmp_path, sid, data, fmt="jsonl")
    loaded = load_export_file(outj)
    assert loaded["messages"]
    nid = await store.import_session_data(loaded, project_root=str(tmp_path))
    assert nid != sid
    todos2 = await store.list_todos(nid)
    assert len(todos2) == 2
    fid = await store.fork_session(sid)
    assert fid != sid
    st = await store.stats_summary()
    assert st["sessions"] >= 3
    await store.close()


def test_agents_fs(tmp_path: Path):
    d = tmp_path / ".takton" / "agents"
    d.mkdir(parents=True)
    (d / "reviewer.md").write_text(
        "---\nname: reviewer\nmode: ask\ndescription: reviews\n---\nBe thorough.\n",
        encoding="utf-8",
    )
    agents = load_agents(tmp_path)
    assert len(agents) == 1
    a = get_agent(tmp_path, "reviewer")
    assert a and "thorough" in a.body.lower()
