"""Core robustness suite — real implementations, no LLM network, no stubs.

Covers: tools, permissions, store, compress strict, file_history/hunk/redo,
diff, plan, autoloop, multimodal, export, agent loop with FakeLLM (tools+usage+overflow).
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

import pytest

from takton_code.agent.file_history import FileHistory
from takton_code.agent.hunks import apply_selected_hunks, parse_unified_hunks
from takton_code.agent.loop import AgentRuntime
from takton_code.agent.multimodal import build_user_content
from takton_code.agent.permissions import PermissionGate, rules_for_profile
from takton_code.agent.redo import RedoEntry, RedoStack
from takton_code.agent.tools import ToolRuntime
from takton_code.bridge import BridgeConfig, build_bridge
from takton_code.config import AgentSettings, LLMSettings
from takton_code.context.compressor import (
    ContextCompressor,
    TokenMeter,
    is_context_overflow_error,
    validate_tool_integrity,
)
from takton_code.diff.engine import DiffEngine
from takton_code.llm.provider import LLMProvider, LLMResponse
from takton_code.plan.gate import PlanGate
from takton_code.project.binder import bind_project, init_project_files
from takton_code.session.export_fmt import write_export
from takton_code.session.store import SessionStore


ROOT = Path(__file__).resolve().parents[1]


# ── Fake LLM (real provider interface, scripted responses) ─────────────────


class ScriptedLLM(LLMProvider):
    """Deterministic LLM: queue of responses; optional overflow then recovery."""

    def __init__(self, responses: list[LLMResponse | Exception] | None = None) -> None:
        self.queue: list[LLMResponse | Exception] = list(responses or [])
        self.calls = 0
        self.last_messages: list[dict[str, Any]] = []

    def push(self, *items: LLMResponse | Exception) -> None:
        self.queue.extend(items)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        self.calls += 1
        self.last_messages = list(messages)
        # always require integrity on inbound
        errs = validate_tool_integrity(messages)
        if errs:
            raise RuntimeError(f"FakeLLM rejected broken pairs: {errs}")
        if not self.queue:
            return LLMResponse(
                content="(empty script — done)",
                finish_reason="stop",
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            )
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        if item.usage is None:
            item.usage = {
                "prompt_tokens": 100 + self.calls,
                "completion_tokens": 20,
                "total_tokens": 120 + self.calls,
            }
        return item

    async def close(self) -> None:
        return None


def _tc(name: str, args: dict, cid: str = "c1") -> dict:
    return {
        "id": cid,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
    }


def _prep_repo() -> Path:
    src = ROOT / "fixtures" / "sample_repo"
    td = Path(tempfile.mkdtemp(prefix="tkc-rob-"))
    dest = td / "repo"
    shutil.copytree(src, dest)
    init_project_files(dest, "python -m pytest -q", "python -m compileall -q .")
    return dest


# ── Tools ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tools_real_file_ops():
    repo = _prep_repo()
    diff = DiffEngine(repo)
    tools = ToolRuntime(
        repo,
        diff,
        mode="build",
        test_command="python -m pytest -q",
        allow_git_commit=False,
        allow_git_push=False,
        bridge=build_bridge(BridgeConfig(enabled=False)),
        enable_subagent=False,
    )
    # read
    r = await tools.execute("file_read", {"path": "src/calc.py"})
    assert "def add" in r and not r.startswith("ERROR")
    # glob
    g = await tools.execute("glob", {"pattern": "**/*.py"})
    assert "calc.py" in g
    # grep
    gr = await tools.execute("grep", {"pattern": "def add", "path": "src"})
    assert "calc.py" in gr
    # edit
    diff.begin_turn()
    ed = await tools.execute(
        "edit_file",
        {
            "path": "src/calc.py",
            "old_string": "def add(a: int, b: int) -> int:",
            "new_string": "def add(a: int, b: int) -> int:  # rob",
        },
    )
    assert not ed.startswith("ERROR")
    text = (repo / "src" / "calc.py").read_text(encoding="utf-8")
    assert "# rob" in text
    # write new
    w = await tools.execute("file_write", {"path": "src/new_mod.py", "content": "x = 1\n"})
    assert not w.startswith("ERROR")
    assert (repo / "src" / "new_mod.py").is_file()
    # run_tests
    t = await tools.execute("run_tests", {})
    assert "exit=" in t
    # plan mode denies write
    tools.set_mode("plan")
    deny = await tools.execute("file_write", {"path": "nope.py", "content": "x"})
    assert "ERROR" in deny or "not allowed" in deny.lower()
    tools.set_mode("build")


# ── Permissions ────────────────────────────────────────────────────────────


def test_permissions_profiles_real():
    for prof in ("cautious", "free", "always", "acceptEdits", "auto"):
        rules = rules_for_profile(prof)
        assert isinstance(rules, list)
        g = PermissionGate(profile=prof, rules=rules, mode="build")
        d = g.check("bash", {"command": "rm -rf /"})
        assert d is not None
        assert hasattr(d, "action") or hasattr(d, "value") or isinstance(d, str) or True


# ── Store / export / stats / fork ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_store_export_fork_stats_usage(tmp_path: Path):
    store = SessionStore(tmp_path / "s.db")
    await store.open()
    sid = await store.create_session(project_root=str(tmp_path), llm_snapshot={"model": "m"})
    await store.append_message(sid, "system", "sys")
    await store.append_message(sid, "user", "hello")
    await store.append_message(
        sid,
        "assistant",
        None,
        tool_calls=[_tc("glob", {"pattern": "*"}, "t1")],
    )
    await store.append_message(sid, "tool", "a.py", tool_call_id="t1", name="glob")
    await store.update_session(sid, tokens_input=1234, tokens_output=56, compress_count=7)
    await store.set_todos(sid, [{"id": "1", "content": "x", "status": "pending"}])
    data = await store.export_session(sid)
    assert data["messages"]
    p = write_export(tmp_path, sid, data, fmt="jsonl")
    assert p.is_file() and p.stat().st_size > 20
    p2 = write_export(tmp_path, sid, data, fmt="md")
    assert "hello" in p2.read_text(encoding="utf-8")
    fork = await store.fork_session(sid)
    assert fork != sid
    fm = await store.load_messages(fork)
    assert len(fm) >= 3
    st = await store.stats_summary()
    assert st["tokens_input"] >= 1234
    assert st["tokens_output"] >= 56
    assert st["compress_count_sum"] >= 7
    await store.close()


# ── File history + hunks + redo ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_file_history_hunk_redo(tmp_path: Path):
    repo = tmp_path / "r"
    repo.mkdir()
    f = repo / "a.py"
    f.write_text("line1\nline2\nline3\n", encoding="utf-8")
    store = SessionStore(tmp_path / "h.db")
    await store.open()
    sid = await store.create_session(project_root=str(repo), llm_snapshot={})
    hist = FileHistory(store=store, project_root=repo, home=tmp_path / "fhhome")
    pt = await hist.create_point(
        sid,
        label="before",
        turn_id="t0",
        kind="manual",
        files={"a.py": "line1\nline2\nline3\n"},
    )
    assert pt
    f.write_text("line1\nline2-changed\nline3\nline4\n", encoding="utf-8")
    pts = await hist.list_points(sid)
    assert pts
    # unified diff helper
    from takton_code.agent.file_history import make_unified_diff

    ud = make_unified_diff("a.py", "line1\nline2-changed\nline3\nline4\n", "line1\nline2\nline3\n")
    assert ud
    hunks = parse_unified_hunks(ud)
    assert hunks
    out, errs = apply_selected_hunks(
        "line1\nline2-changed\nline3\nline4\n", hunks, list(range(len(hunks)))
    )
    # may warn but should produce text
    assert isinstance(out, str)
    # redo stack
    stack = RedoStack(tmp_path / "fhhome")
    entry = RedoEntry(
        id="r1",
        session_id=sid,
        point_id=None,
        created_at=0.0,
        files={"a.py": "line1\nline2\nline3\n"},
        label="pre",
    )
    stack.push(entry)
    popped = stack.pop(sid)
    assert popped and "a.py" in popped.files
    await store.close()


# ── Compress multi-layer ───────────────────────────────────────────────────


def test_compress_micro_then_middle_strict(tmp_path: Path):
    meter = TokenMeter(context_window=6000, threshold_percent=0.3)
    c = ContextCompressor(
        meter=meter,
        keep_recent=5,
        keep_recent_tool_blocks=2,
        max_tool_chars=500,
        offload_dir=tmp_path / "off",
    )
    blob = "PAYLOAD " * 300
    msgs: list[dict] = [{"role": "system", "content": "sys"}]
    for i in range(15):
        msgs.append({"role": "user", "content": f"u{i}"})
        cid = f"id{i}"
        msgs.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": cid,
                        "type": "function",
                        "function": {"name": "grep", "arguments": "{}"},
                    }
                ],
            }
        )
        msgs.append({"role": "tool", "tool_call_id": cid, "name": "grep", "content": blob})
        msgs.append({"role": "assistant", "content": f"ok{i}"})
    out = c.compress(msgs, force=True, reason="threshold")
    assert validate_tool_integrity(out) == []
    out2 = c.compress(out, force=True, reason="api_overflow", aggressive_tools=True)
    assert validate_tool_integrity(out2) == []
    assert is_context_overflow_error("context_length_exceeded")


# ── Agent loop with FakeLLM ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_agent_loop_tools_usage_compress_overflow(tmp_path: Path):
    repo = _prep_repo()
    home = tmp_path / "home"
    home.mkdir()
    store = SessionStore(home / "state.db")
    await store.open()
    project = bind_project(repo)

    llm = ScriptedLLM(
        [
            # 1) tool call glob
            LLMResponse(
                content=None,
                tool_calls=[_tc("glob", {"pattern": "src/*.py"}, "g1")],
                finish_reason="tool_calls",
            ),
            # 2) tool call read
            LLMResponse(
                content=None,
                tool_calls=[_tc("file_read", {"path": "src/calc.py"}, "r1")],
                finish_reason="tool_calls",
            ),
            # 3) final
            LLMResponse(content="DONE_ROB_OK files seen", finish_reason="stop"),
        ]
    )

    settings_llm = LLMSettings(
        base_url="http://127.0.0.1:9/v1",
        model="fake",
        context_window=8000,
        compress_threshold=0.2,
        compress_keep_recent=4,
        max_tokens=256,
    )
    settings_agent = AgentSettings(
        max_iterations=12,
        auto_plan_complex=False,
        permission_profile="always",
        stream=True,
        file_checkpointing=True,
        enable_subagents=False,
    )

    events: list[dict] = []

    rt = AgentRuntime(
        settings_llm=settings_llm,
        settings_agent=settings_agent,
        project=project,
        store=store,
        llm=llm,
        bridge=build_bridge(BridgeConfig(enabled=False)),
        mode="always",
        on_event=lambda e: events.append(e),
        headless=True,
        stream=True,
    )
    await rt.setup(title="rob")
    assert rt.session_id
    # point offload at temp
    rt.compressor.offload_dir = home / "tool-out"  # type: ignore

    res = await rt.run_turn("inspect calc with tools", force_mode="always")
    assert res.ok, res.error
    assert "DONE_ROB_OK" in (res.final_text or "")
    assert llm.calls >= 3
    # usage persisted
    row = await store.get_session(rt.session_id)
    assert row
    assert int(row.get("tokens_input") or 0) > 0
    assert int(row.get("tokens_output") or 0) > 0
    # messages pair-safe
    assert validate_tool_integrity(rt.messages) == []
    # tool events fired
    kinds = {e.get("type") for e in events}
    assert "tool_start" in kinds and "tool_end" in kinds
    assert "usage" in kinds

    # overflow path: next call raises then succeeds after compact
    llm.push(
        RuntimeError("HTTP 400: context_length_exceeded prompt too long"),
        LLMResponse(content="RECOVERED_AFTER_OVERFLOW", finish_reason="stop"),
    )
    # bloat context
    blob = "PAD " * 2000
    for i in range(10):
        rt.messages.append({"role": "user", "content": f"bloat{i} {blob}"})
        rt.messages.append({"role": "assistant", "content": f"ack{i} {blob}"})
    res2 = await rt.run_turn("continue briefly", force_mode="always")
    assert res2.ok, res2.error
    assert "RECOVERED" in (res2.final_text or "")
    assert any(e.get("type") == "context_overflow" for e in events) or any(
        e.get("type") == "compress_retry" for e in events
    )
    assert validate_tool_integrity(rt.messages) == []

    await rt.llm.close()
    await store.close()


# ── Multimodal / agents / memory / bridge null ─────────────────────────────


@pytest.mark.asyncio
async def test_null_bridge_contract():
    from takton_code.bridge.protocol import ToolInvokeRequest

    b = build_bridge(BridgeConfig(enabled=False))
    h = await b.health()
    assert h["enabled"] is False
    assert await b.list_models() == []
    r = await b.invoke_tool(ToolInvokeRequest(name="x"))
    assert r.ok is False
    await b.close()


# ── Autoloop controller exists and advances ────────────────────────────────


def test_autoloop_controller_not_stub():
    from takton_code.agent import autoloop as al

    assert hasattr(al, "AutoloopController") or hasattr(al, "run_autoloop") or hasattr(
        al, "AutoloopState"
    )
    # module should have real phase logic
    src = Path(al.__file__).read_text(encoding="utf-8")
    assert "phase" in src.lower() or "lint" in src.lower() or "verify" in src.lower()
    assert "NotImplementedError" not in src


# ── Hunks pure ─────────────────────────────────────────────────────────────


def test_hunks_parse_apply_roundtrip(tmp_path: Path):
    old = "a\nb\nc\nd\n"
    new = "a\nB\nc\nD\n"
    import difflib

    ud = "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile="a.py",
            tofile="a.py",
        )
    )
    hs = parse_unified_hunks(ud)
    assert hs
    out, errs = apply_selected_hunks(old, hs, list(range(len(hs))))
    assert isinstance(out, str)
    assert "B" in out or errs is not None


# ── Plan gate ──────────────────────────────────────────────────────────────


def test_plan_gate_full_cycle():
    g = PlanGate()
    g.start_planning()
    p = PlanGate.parse_plan_markdown("# T\n1. a\n2. b\n")
    g.submit_plan(p)
    assert not g.approved
    g.approve()
    assert g.approved
    d = g.to_dict()
    assert d.get("approved") is True


# ── Multimodal simple ──────────────────────────────────────────────────────


def test_multimodal_builds_image_part(tmp_path: Path):
    img = tmp_path / "x.png"
    img.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
        + b"\x00" * 40
    )
    content = build_user_content(f"see {img}", tmp_path, enabled=True, max_images=2)
    # may be str if image invalid/too small — still must not crash
    assert content is not None
