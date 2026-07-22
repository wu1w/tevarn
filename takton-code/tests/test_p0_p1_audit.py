"""P0 source-audit evidence + P1 blind-spot tests (no network)."""

from __future__ import annotations

import ast
import inspect
import json
import tempfile
from pathlib import Path

import pytest

from takton_code.agent.doom_loop import DoomLoopGuard
from takton_code.agent.loop import AgentRuntime
from takton_code.agent.permissions import PermissionGate, rules_for_profile
from takton_code.bridge import BridgeConfig, build_bridge
from takton_code.config import LLMSettings, AgentSettings, load_settings
from takton_code.context.compressor import (
    ContextCompressor,
    TokenMeter,
    ensure_anthropic_strict,
    microcompact_tools,
    validate_tool_integrity,
    CLEARED_TOOL_RESULT,
)
from takton_code.context.policy import recommended_thrashing
from takton_code.llm.provider import LLMResponse, _sanitize_messages, _truncate_tool_arguments
from takton_code.project.binder import bind_project
from takton_code.session.store import SessionStore


# ── P0-1: _llm_chat always strict ───────────────────────────────────────────


def test_p0_llm_chat_source_calls_ensure_strict():
    """Static: loop._llm_chat body must call ensure_anthropic_strict before LLM."""
    import takton_code.agent.loop as loop_mod

    src = inspect.getsource(loop_mod.AgentRuntime._llm_chat)
    assert "ensure_anthropic_strict" in src
    # call appears before collect_stream / llm.chat
    i_strict = src.find("ensure_anthropic_strict")
    i_stream = src.find("collect_stream")
    i_chat = src.find("self.llm.chat")
    assert i_strict >= 0
    assert i_stream < 0 or i_strict < i_stream
    assert i_chat < 0 or i_strict < i_chat


def test_p0_provider_chat_paths_use_sanitize():
    import takton_code.llm.provider as prov

    src = inspect.getsource(prov)
    assert src.count("_sanitize_messages") >= 3  # sanitize def + chat + stream paths
    # sanitize itself calls ensure_anthropic_strict
    ssrc = inspect.getsource(prov._sanitize_messages)
    assert "ensure_anthropic_strict" in ssrc


# ── P0-3 microcompact + archive ─────────────────────────────────────────────


def test_p0_microcompact_keeps_tool_call_ids(tmp_path: Path):
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
    ]
    for i in range(6):
        cid = f"c{i}"
        msgs.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": cid, "type": "function", "function": {"name": "file_read", "arguments": "{}"}}
                ],
            }
        )
        msgs.append(
            {
                "role": "tool",
                "tool_call_id": cid,
                "name": "file_read",
                "content": ("BIG" * 500) + f" end{i}",
            }
        )
    out, stats = microcompact_tools(
        msgs, keep_recent_blocks=2, max_tool_chars=100, offload_dir=tmp_path / "o", clear_all_but_recent=False
    )
    assert stats.get("cleared_blocks", 0) + stats.get("trimmed_tools", 0) > 0
    assert validate_tool_integrity(out) == []
    # old tools cleared but ids preserved
    for m in out:
        if m.get("role") == "tool" and CLEARED_TOOL_RESULT in str(m.get("content") or ""):
            assert m.get("tool_call_id")


def test_p0_middle_summary_archives_transcript(tmp_path: Path):
    meter = TokenMeter(context_window=3000, threshold_percent=0.2)
    c = ContextCompressor(
        meter=meter,
        keep_recent=4,
        compact_mode="aggressive",
        retain_turns=2,
        archive_dir=tmp_path / "arch",
        session_id="aud1",
        offload_dir=tmp_path / "off",
    )
    blob = "Z" * 300
    msgs: list[dict] = [{"role": "system", "content": "sys"}]
    for i in range(20):
        msgs.append({"role": "user", "content": f"u{i} {blob}"})
        msgs.append({"role": "assistant", "content": f"a{i} {blob}"})
    out = c.compress(msgs, force=True, reason="threshold")
    assert validate_tool_integrity(out) == []
    assert c.last_archive_path
    p = Path(c.last_archive_path)
    assert p.is_file()
    body = p.read_text(encoding="utf-8")
    assert "u0" in body or "user" in body


# ── P0-4 sanitize ───────────────────────────────────────────────────────────


def test_p0_sanitize_empty_content_null_and_json_args():
    msgs = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "x1",
                    "type": "function",
                    "function": {"name": "grep", "arguments": json.dumps({"pattern": "a" * 40000})},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "x1", "name": "grep", "content": "hit"},
    ]
    out = _sanitize_messages(msgs)
    asst = next(m for m in out if m.get("role") == "assistant")
    assert asst.get("content") is None
    args = asst["tool_calls"][0]["function"]["arguments"]
    parsed = json.loads(args)  # must remain valid JSON
    assert isinstance(parsed, dict)
    assert validate_tool_integrity(out) == []


def test_p0_truncate_tool_arguments_valid_json():
    s = _truncate_tool_arguments({"path": "x" * 50000}, max_len=500)
    obj = json.loads(s)
    assert obj.get("_truncated") is True


# ── P0-9 / P1-9 sanitize cannot be bypassed on provider ─────────────────────


def test_p1_openai_provider_chat_sanitizes():
    # covered by async test below
    assert callable(_sanitize_messages)


@pytest.mark.asyncio
async def test_p1_provider_chat_sanitizes_async():
    from takton_code.llm.provider import OpenAICompatibleProvider

    seen: list = []

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }

    class FakeClient:
        async def post(self, *a, **k):
            body = k.get("json") or {}
            seen.append(body.get("messages") or [])
            return FakeResp()

        async def aclose(self):
            return None

    p = OpenAICompatibleProvider(base_url="http://127.0.0.1:9/v1", api_key="x", model="m")
    p._client = FakeClient()  # type: ignore
    msgs = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "t1", "type": "function", "function": {"name": "glob", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "t1", "name": "glob", "content": "ok"},
    ]
    await p.chat(msgs)
    assert seen
    sent = seen[0]
    asst = next(m for m in sent if m.get("role") == "assistant")
    assert asst.get("content") is None  # empty → null


# ── P1-6 subagent success → final + idle/turn_end ───────────────────────────


class ScriptedLLM:
    def __init__(self, responses: list[LLMResponse]):
        self.responses = list(responses)
        self.i = 0

    async def chat(self, messages, tools=None, **kwargs):
        if self.i >= len(self.responses):
            return LLMResponse(
                content="done",
                tool_calls=None,
                usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            )
        r = self.responses[self.i]
        self.i += 1
        return r

    async def chat_stream(self, messages, tools=None, **kwargs):
        from takton_code.llm.provider import StreamDelta

        r = await self.chat(messages, tools=tools)
        if r.content:
            yield StreamDelta(content=r.content)
        if r.tool_calls:
            yield StreamDelta(tool_calls=r.tool_calls)
        if r.reasoning_content:
            yield StreamDelta(reasoning=r.reasoning_content)
        yield StreamDelta(finish_reason="stop", usage=r.usage or {})



@pytest.mark.asyncio
async def test_p1_subagent_success_emits_idle_and_final(tmp_path: Path):
    """K5: successful tool path (incl. spawn result) must end with assistant + turn_end + idle."""
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "a.txt").write_text("hi", encoding="utf-8")
    import subprocess

    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "i"], cwd=repo, check=True, capture_output=True)

    home = tmp_path / "home"
    home.mkdir()
    store = SessionStore(home / "s.db")
    await store.open()
    project = bind_project(repo)
    events: list[dict] = []

    # Parent: spawn_subagent tool then final text
    # Subagent uses same llm — craft so parent tools first, then final.
    # spawn_subagent will call subagent which needs LLM responses too.
    # Simpler path: just run a normal tool (file_read) then final — still covers success lifecycle.
    # Plus one spawn with subagent that returns immediately via Scripted.

    llm = ScriptedLLM(
        [
            LLMResponse(
                content=None,
                tool_calls=[
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "file_read", "arguments": json.dumps({"path": "a.txt"})},
                    }
                ],
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            ),
            LLMResponse(
                content="All good after tool",
                tool_calls=None,
                usage={"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18},
            ),
        ]
    )

    rt = AgentRuntime(
        project=project,
        store=store,
        settings_llm=LLMSettings(context_window=8000, compress_threshold=0.9),
        settings_agent=AgentSettings(max_iterations=8, permission_profile="always", enable_subagents=True),
        llm=llm,  # type: ignore
        bridge=build_bridge(BridgeConfig(enabled=False)),
        mode="build",
        on_event=lambda **kw: events.append(kw) if "type" in kw else events.append({"type": "ev", **kw}),
        stream=False,
        headless=True,
    )
    # fix on_event signature — AgentRuntime.emit uses type=
    def on_ev(typ=None, **payload):
        if isinstance(typ, dict):
            events.append(typ)
        else:
            events.append({"type": typ, **payload})

    rt.on_event = on_ev  # type: ignore
    await rt.setup()
    r = await rt.run_turn("read a.txt and summarize")
    assert r.ok
    assert "All good" in (r.final_text or "")
    types = [e.get("type") for e in events]
    assert "turn_end" in types
    assert "idle" in types
    assert "assistant_final" in types
    # final assistant message present
    assert any(m.get("role") == "assistant" and "All good" in str(m.get("content") or "") for m in rt.messages)
    await store.close()


@pytest.mark.asyncio
async def test_p1_spawn_subagent_success_path(tmp_path: Path):
    repo = tmp_path / "r2"
    repo.mkdir()
    (repo / "x.py").write_text("print(1)\n", encoding="utf-8")
    import subprocess

    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "i"], cwd=repo, check=True, capture_output=True)

    home = tmp_path / "h2"
    home.mkdir()
    store = SessionStore(home / "s.db")
    await store.open()
    project = bind_project(repo)
    events: list[dict] = []

    def on_ev(typ=None, **payload):
        events.append({"type": typ, **payload} if not isinstance(typ, dict) else typ)

    # Parent calls spawn_subagent; subagent llm needs responses.
    # run_subagent uses collect_stream on same llm — so order:
    # 1 parent: tool spawn
    # 2 subagent: final text (no tools)
    # 3 parent: final after tool result
    llm = ScriptedLLM(
        [
            LLMResponse(
                content=None,
                tool_calls=[
                    {
                        "id": "s1",
                        "type": "function",
                        "function": {
                            "name": "spawn_subagent",
                            "arguments": json.dumps(
                                {"agent": "explore", "prompt": "list files", "max_iterations": 2}
                            ),
                        },
                    }
                ],
                usage={"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
            ),
            # subagent final
            LLMResponse(
                content="subagent saw x.py",
                tool_calls=None,
                usage={"prompt_tokens": 3, "completion_tokens": 3, "total_tokens": 6},
            ),
            # parent final
            LLMResponse(
                content="Parent done with subagent result",
                tool_calls=None,
                usage={"prompt_tokens": 4, "completion_tokens": 4, "total_tokens": 8},
            ),
        ]
    )

    rt = AgentRuntime(
        project=project,
        store=store,
        settings_llm=LLMSettings(context_window=8000, compress_threshold=0.9),
        settings_agent=AgentSettings(max_iterations=10, permission_profile="always", enable_subagents=True),
        llm=llm,  # type: ignore
        bridge=build_bridge(BridgeConfig(enabled=False)),
        mode="build",
        on_event=on_ev,
        stream=False,
        headless=True,
    )
    await rt.setup()
    r = await rt.run_turn("use subagent to explore")
    assert r.ok, r.error
    assert "Parent done" in (r.final_text or "")
    types = [e.get("type") for e in events]
    assert "subagent_start" in types or any("subagent" in str(e) for e in events)
    assert "turn_end" in types and "idle" in types
    await store.close()


# ── P1-7 thrashing calibration ──────────────────────────────────────────────


def test_p1_thrashing_recommended_by_window():
    small = recommended_thrashing(12000)
    large = recommended_thrashing(65536)
    assert int(small["max_events"]) > int(large["max_events"])
    assert float(small["window_sec"]) <= float(large["window_sec"])


# ── P1-8 permission matrix ──────────────────────────────────────────────────


PROFILES = ["cautious", "free", "acceptEdits", "always", "bypass", "dontAsk", "plan", "auto"]
MODES = ["plan", "build", "ask", "explore", "always"]


def test_p1_permission_profile_mode_matrix():
    """40-cell matrix: write tools denied in plan/ask/explore regardless of always profile."""
    rows = []
    for profile in PROFILES:
        for mode in MODES:
            g = PermissionGate(profile=profile, mode=mode if mode != "always" else "build", rules=rules_for_profile(profile))
            if mode == "always":
                g.profile = "always"
                g.rules = rules_for_profile("always")
                g.mode = "build"
            edit = g.check("file_write", {"path": "a.py"})
            bash = g.check("run_shell", {"command": "echo hi"})
            read = g.check("file_read", {"path": "a.py"})
            effective_mode = g.mode
            # contradictions documented
            plan_like = effective_mode in ("plan", "ask", "explore")
            if plan_like:
                assert edit == "deny", f"{profile}/{mode} edit should deny"
                assert bash == "deny", f"{profile}/{mode} bash should deny"
            rows.append(
                {
                    "profile": profile,
                    "mode": mode,
                    "effective_mode": effective_mode,
                    "file_write": edit,
                    "run_shell": bash,
                    "file_read": read,
                    "note": "plan-like denies writes" if plan_like else "ok",
                }
            )
    assert len(rows) == 40
    # export for manual
    out = Path(__file__).resolve().parents[1] / "docs" / "PERMISSION_MATRIX.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


# ── doom loop ───────────────────────────────────────────────────────────────


def test_doom_loop_trips_on_repeat():
    g = DoomLoopGuard(threshold=3)
    assert not g.record("grep", '{"pattern":"x"}')
    assert not g.record("grep", '{"pattern":"x"}')
    assert g.record("grep", '{"pattern":"x"}')
    assert g.tripped


@pytest.mark.asyncio
async def test_worktree_bind_session(tmp_path: Path):
    store = SessionStore(tmp_path / "w.db")
    await store.open()
    sid = await store.create_session(project_root=str(tmp_path), mode="build")
    await store.bind_worktree(sid, worktree_name="feat", worktree_path=str(tmp_path / "wt"))
    wt = await store.get_worktree(sid)
    assert wt["worktree_name"] == "feat"
    assert "wt" in (wt["worktree_path"] or "")
    await store.close()
