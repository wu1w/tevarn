"""Unit tests (no LLM required)."""

from __future__ import annotations

from pathlib import Path

import pytest

from takton_code.context.compressor import ContextCompressor, TokenMeter, estimate_messages
from takton_code.diff.engine import DiffEngine
from takton_code.plan.gate import PlanGate, should_auto_plan
from takton_code.project.binder import bind_project


def test_should_auto_plan():
    assert should_auto_plan(
        "refactor the auth module architecture", auto_plan_complex=True, simple_max_chars=80
    )
    assert not should_auto_plan("fix typo", auto_plan_complex=True, simple_max_chars=80)


def test_plan_parse():
    md = """# Add rate limit
Summary: protect login
1. Find login handler in `api/auth.py`
2. Add middleware
3. Tests
Risks: breaking clients
Test plan: pytest tests/test_auth.py
"""
    p = PlanGate.parse_plan_markdown(md)
    assert len(p.steps) >= 2
    g = PlanGate()
    g.start_planning()
    g.submit_plan(p)
    g.approve()
    assert g.approved


def test_diff_engine(tmp_path: Path):
    root = tmp_path
    (root / "a.txt").write_text("hello\n", encoding="utf-8")
    d = DiffEngine(root)
    d.begin_turn()
    d.snapshot_before("a.txt")
    (root / "a.txt").write_text("hello\nworld\n", encoding="utf-8")
    ch = d.record_after("a.txt")
    assert ch and ch.op == "modify"
    assert "world" in ch.unified_diff()
    msg = d.revert("a.txt")
    assert "reverted" in msg
    assert (root / "a.txt").read_text(encoding="utf-8") == "hello\n"


def test_compress_five_times():
    meter = TokenMeter(context_window=2000, threshold_percent=0.3)
    c = ContextCompressor(
        meter=meter,
        keep_recent=4,
        compact_mode="aggressive",
        retain_turns=2,
    )
    msgs = [{"role": "system", "content": "sys"}]
    blob = "x" * 400
    for i in range(40):
        msgs.append({"role": "user", "content": f"u{i} {blob}"})
        msgs.append({"role": "assistant", "content": f"a{i} {blob}"})
    before = estimate_messages(msgs)
    for i in range(6):
        msgs = c.compress(msgs, force=True, reason=f"t{i}")
    assert c.compress_count >= 5
    assert msgs[0]["role"] == "system"
    assert estimate_messages(msgs) < before


def test_binder_sample():
    root = Path(__file__).resolve().parents[1] / "fixtures" / "sample_repo"
    ctx = bind_project(root)
    assert "python" in ctx.languages
    assert ctx.test_command


@pytest.mark.asyncio
async def test_session_store(tmp_path: Path):
    from takton_code.session.store import SessionStore

    s = SessionStore(tmp_path / "t.db")
    await s.open()
    sid = await s.create_session(project_root=str(tmp_path), llm_snapshot={"model": "m"})
    row = await s.get_session(sid)
    assert row and row.get("slug")
    await s.append_message(sid, "user", "hi")
    await s.append_message(sid, "assistant", "yo")
    msgs = await s.load_messages(sid)
    assert len(msgs) == 2
    await s.append_part(sid, {"id": "p1", "type": "text", "text": "x"})
    parts = await s.load_parts(sid)
    assert parts and parts[0]["type"] == "text"
    await s.enqueue_prompt(sid, "q1")
    assert len(await s.list_queue(sid)) == 1
    assert (await s.dequeue_prompt(sid))["content"] == "q1"
    await s.save_file_snapshot(sid, "t1", "a.py", "old")
    assert (await s.load_turn_snapshots(sid, "t1"))[0]["content"] == "old"
    await s.set_setting("a", {"b": 1})
    assert (await s.get_setting("a"))["b"] == 1
    fork = await s.fork_session(sid)
    assert fork != sid
    exp = await s.export_session(sid)
    assert exp["format"] == "takton-code-session-v1"
    await s.close()


def test_parts_model():
    from takton_code.agent.parts import part_step_start, part_text, part_tool_start

    p = part_text("hi", role_hint="user")
    assert p.type == "text"
    assert part_step_start(1).type == "step-start"
    assert part_tool_start("read", "c1", "{}").state["status"] == "running"


def test_system_prompt_neutral_and_mode():
    from takton_code.agent.prompt import CODE_SYSTEM, build_system_prompt

    assert "Takton Code" in CODE_SYSTEM
    assert "Claude Code" not in CODE_SYSTEM
    assert "Anthropic" not in CODE_SYSTEM
    assert "phone home" in CODE_SYSTEM.lower() or "phone-home" in CODE_SYSTEM.lower()
    assert "over-engineering" in CODE_SYSTEM or "Smallest change" in CODE_SYSTEM
    plan = build_system_prompt(mode="plan", project_block="root=/tmp/demo")
    assert "READ-ONLY" in plan
    assert "Plan mode lock" in plan
    assert "root=/tmp/demo" in plan
    build = build_system_prompt(mode="build", project_block="")
    assert "Build mode" in build
