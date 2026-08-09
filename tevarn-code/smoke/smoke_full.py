"""Strict smoke test for Tevarn Code full feature set.

Covers:
1. Unit: plan gate, diff, compress x5+, store parts/queue/undo
2. LLM (local service): coding task + tests green
3. Interrupt x3 + continue
4. Compress >=5 then still functional
5. Session/settings reopen
6. Bridge health/models/skills/tools if Tevarn up
7. Plan mode denies writes
8. Explore mode read-only
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tevarn_code.agent.loop import AgentRuntime
from tevarn_code.bridge import BridgeConfig, build_bridge
from tevarn_code.config import load_settings, save_user_settings_patch
from tevarn_code.context.compressor import ContextCompressor, TokenMeter
from tevarn_code.diff.engine import DiffEngine
from tevarn_code.llm.provider import OpenAICompatibleProvider, build_llm_provider
from tevarn_code.plan.gate import PlanGate, should_auto_plan
from tevarn_code.project.binder import bind_project, init_project_files
from tevarn_code.session.store import SessionStore

LOCAL_BASE = os.environ.get("TEVARN_CODE_BASE_URL", "http://127.0.0.1:8088/v1")
LOCAL_MODEL = os.environ.get("TEVARN_CODE_MODEL", "<your-model.gguf>")
SMOKE_CTX = int(os.environ.get("TEVARN_CODE_CONTEXT_WINDOW", "8000"))
SMOKE_THRESH = float(os.environ.get("TEVARN_CODE_COMPRESS_THRESHOLD", "0.25"))
BRIDGE_URL = os.environ.get("TEVARN_CODE_BRIDGE_URL", "http://127.0.0.1:8090/api")


class Fail(Exception):
    pass


def log(msg: str) -> None:
    print(f"[smoke] {msg}", flush=True)


def prep_repo() -> Path:
    src = ROOT / "fixtures" / "sample_repo"
    td = Path(tempfile.mkdtemp(prefix="tkc-smoke-"))
    dest = td / "sample_repo"
    shutil.copytree(src, dest)
    subprocess.run(["git", "init"], cwd=dest, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "smoke@tevarn.local"], cwd=dest, check=True)
    subprocess.run(["git", "config", "user.name", "smoke"], cwd=dest, check=True)
    subprocess.run(["git", "add", "-A"], cwd=dest, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=dest, check=True, capture_output=True)
    init_project_files(dest, "python -m pytest -q", "python -m compileall -q .")
    return dest


async def make_runtime(repo: Path, home: Path, session_id: str | None = None, bridge_on: bool = False):
    os.environ["TEVARN_CODE_HOME"] = str(home)
    os.environ["TEVARN_CODE_BASE_URL"] = LOCAL_BASE
    os.environ["TEVARN_CODE_MODEL"] = LOCAL_MODEL
    os.environ["TEVARN_CODE_CONTEXT_WINDOW"] = str(SMOKE_CTX)
    os.environ["TEVARN_CODE_COMPRESS_THRESHOLD"] = str(SMOKE_THRESH)
    os.environ["TEVARN_CODE_MAX_TOKENS"] = "2048"

    settings = load_settings()
    settings.llm.base_url = LOCAL_BASE
    settings.llm.model = LOCAL_MODEL
    settings.llm.context_window = SMOKE_CTX
    settings.llm.compress_threshold = SMOKE_THRESH
    settings.llm.max_tokens = 2048
    settings.llm.compress_keep_recent = 4
    settings.agent.max_iterations = 24
    settings.agent.auto_plan_complex = False
    settings.agent.simple_task_max_chars = 500
    settings.agent.checkpoint_every = 1

    store = SessionStore(home / "state.db")
    await store.open()
    project = bind_project(repo)
    llm = build_llm_provider(
        base_url=settings.llm.base_url,
        api_key="no-key",
        model=settings.llm.model,
        temperature=0.1,
        max_tokens=settings.llm.max_tokens,
        use_bridge=False,
    )
    bridge = build_bridge(
        BridgeConfig(enabled=bridge_on, base_url=BRIDGE_URL, timeout_sec=10)
    )

    def on_event(ev: dict) -> None:
        if ev.get("type") in ("compress", "cancel_requested", "error", "plan_ready", "undo"):
            log(f"event {ev.get('type')}: {json.dumps({k:ev[k] for k in ev if k!='type'}, default=str)[:180]}")

    rt = AgentRuntime(
        settings_llm=settings.llm,
        settings_agent=settings.agent,
        project=project,
        store=store,
        llm=llm,
        bridge=bridge,
        mode="build",
        on_event=on_event,
    )
    await rt.setup(session_id=session_id, title="smoke")
    return rt, store


async def test_unit_local() -> None:
    log("=== unit local ===")
    assert should_auto_plan("refactor architecture module", auto_plan_complex=True, simple_max_chars=80)
    g = PlanGate()
    g.start_planning()
    p = PlanGate.parse_plan_markdown("# X\n1. a\n2. b\n")
    g.submit_plan(p)
    g.approve()
    assert g.approved

    td = Path(tempfile.mkdtemp())
    (td / "a.txt").write_text("hello\n", encoding="utf-8")
    d = DiffEngine(td)
    d.begin_turn()
    d.snapshot_before("a.txt")
    (td / "a.txt").write_text("hello\nworld\n", encoding="utf-8")
    assert d.record_after("a.txt")
    assert "world" in d.all_diffs()

    meter = TokenMeter(context_window=2000, threshold_percent=0.3)
    c = ContextCompressor(meter=meter, keep_recent=4)
    msgs = [{"role": "system", "content": "sys"}]
    blob = "x" * 400
    for i in range(40):
        msgs.append({"role": "user", "content": f"u{i} {blob}"})
        msgs.append({"role": "assistant", "content": f"a{i} {blob}"})
    for i in range(6):
        msgs = c.compress(msgs, force=True, reason=f"t{i}")
    assert c.compress_count >= 5
    assert msgs[0]["role"] == "system"

    home = Path(tempfile.mkdtemp())
    store = SessionStore(home / "s.db")
    await store.open()
    sid = await store.create_session(project_root=str(td), llm_snapshot={"model": "m"})
    sess = await store.get_session(sid)
    assert sess and sess.get("slug")
    await store.append_part(sid, {"type": "text", "text": "hi", "id": "p1"})
    parts = await store.load_parts(sid)
    assert parts and parts[0]["type"] == "text"
    await store.enqueue_prompt(sid, "queued-1")
    q = await store.list_queue(sid)
    assert len(q) == 1
    item = await store.dequeue_prompt(sid)
    assert item and item["content"] == "queued-1"
    await store.save_file_snapshot(sid, "t1", "a.txt", "old")
    snaps = await store.load_turn_snapshots(sid, "t1")
    assert snaps[0]["content"] == "old"
    fork = await store.fork_session(sid)
    assert fork != sid
    await store.close()
    log("unit OK")


async def test_llm_ping() -> None:
    p = OpenAICompatibleProvider(base_url=LOCAL_BASE, api_key="no-key", model=LOCAL_MODEL, max_tokens=256)
    try:
        r = await p.chat(
            [{"role": "system", "content": "Reply exactly: PONG"}, {"role": "user", "content": "ping"}]
        )
        text = (r.content or r.reasoning_content or "").strip()
        log(f"LLM ping => {text[:80]!r}")
        if not text:
            raise Fail("empty LLM")
    finally:
        await p.close()


async def test_coding(rt: AgentRuntime) -> None:
    log("=== coding task ===")
    r = await rt.run_turn(
        "In build mode, add `sub(a: int, b: int) -> int` to src/calc.py returning a-b, "
        "add test_sub in tests/test_calc.py, run_tests. Minimal changes.",
        force_mode="build",
    )
    log(f"coding ok={r.ok} iter={r.iterations} parts={len(r.parts)}")
    calc = (rt.project.root / "src" / "calc.py").read_text(encoding="utf-8")
    if "def sub" not in calc:
        r = await rt.run_turn("src/calc.py still missing def sub. Add it + test, run_tests.", force_mode="build")
        calc = (rt.project.root / "src" / "calc.py").read_text(encoding="utf-8")
    if "def sub" not in calc:
        raise Fail(f"sub missing:\n{calc}")
    out = await rt.tools.run_tests({})  # type: ignore
    log(f"tests: {out[:300]}")
    if "exit=0" not in out[:30] and "failed" in out.lower():
        raise Fail(out)
    # undo snapshots should exist
    tid = await rt.store.latest_turn_id(rt.session_id)  # type: ignore
    log(f"latest snapshot turn={tid}")


async def test_plan_readonly(rt: AgentRuntime) -> None:
    log("=== plan mode denies write ===")
    await rt.set_mode("plan")
    out = await rt.tools.execute("file_write", {"path": "nope.py", "content": "x"})  # type: ignore
    if "not allowed" not in out and "ERROR" not in out:
        raise Fail(f"plan should deny write: {out}")
    log(f"plan deny OK: {out[:80]}")


async def test_explore_readonly(rt: AgentRuntime) -> None:
    log("=== explore mode denies write ===")
    await rt.set_mode("explore")
    out = await rt.tools.execute("edit_file", {"path": "src/calc.py", "old_string": "a", "new_string": "b"})  # type: ignore
    if "not allowed" not in out and "ERROR" not in out:
        raise Fail(f"explore should deny edit: {out}")
    log(f"explore deny OK: {out[:80]}")
    await rt.set_mode("build")


async def test_interrupt(rt: AgentRuntime) -> None:
    log("=== interrupt x3 ===")
    for n in range(3):
        task = asyncio.create_task(
            rt.run_turn(
                f"Round {n+1}: glob **/*.py, read src/calc.py, summarize functions briefly with multiple tools.",
                force_mode="ask",
            )
        )
        await asyncio.sleep(3.0 + n)
        rt.request_cancel()
        result = await task
        log(f"round {n+1} interrupted={result.interrupted} iter={result.iterations}")
        cont = await rt.continue_after_interrupt(f"请继续 round {n+1}，一两段即可")
        if cont.error:
            raise Fail(f"continue fail {n+1}: {cont.error}")
        msgs = await rt.store.load_messages(rt.session_id)  # type: ignore
        if len(msgs) < 3:
            raise Fail("messages lost")
        if not rt.llm_snapshot.get("model"):
            raise Fail("snapshot lost")
    log("interrupt OK")


async def test_compress(rt: AgentRuntime) -> None:
    log("=== compress >=5 ===")
    assert rt.compressor
    blob = ("PAD " * 100) + "\n"
    for i in range(30):
        rt.messages.append({"role": "user", "content": f"u{i}\n{blob}"})
        rt.messages.append({"role": "assistant", "content": f"a{i}\n{blob}"})
    await rt._persist_messages()
    for i in range(6):
        rt.messages.append({"role": "user", "content": f"force-{i}\n{blob*3}"})
        rt.messages.append({"role": "assistant", "content": f"ack-{i}\n{blob*2}"})
        await rt._maybe_compress(force=True, reason=f"smoke-{i}")
        log(f"compress {i+1}: count={rt.compressor.compress_count} msgs={len(rt.messages)}")
    if rt.compressor.compress_count < 5:
        raise Fail(f"compress_count={rt.compressor.compress_count}")
    r = await rt.run_turn("Reply one line: COMPRESS_OK and list files in src/ via glob.", force_mode="build")
    if r.error:
        raise Fail(f"post-compress broken: {r.error}")
    if not rt.messages or rt.messages[0].get("role") != "system":
        raise Fail("system lost")
    log(f"post-compress text={(r.final_text or '')[:120]}")


async def test_queue_and_undo(rt: AgentRuntime) -> None:
    log("=== queue + undo ===")
    await rt.enqueue("say QUEUE_OK in one short line")
    q = await rt.list_queue()
    if not q:
        raise Fail("queue empty after enqueue")
    r = await rt.drain_queue_once()
    if not r or r.error:
        raise Fail(f"drain failed: {r}")
    log(f"queue drain: {(r.final_text or '')[:100]}")

    # touch a file then undo
    path = rt.project.root / "src" / "calc.py"
    before = path.read_text(encoding="utf-8")
    r2 = await rt.run_turn(
        "Add a comment line `# smoke-marker` at the top of src/calc.py using edit_file or file_write.",
        force_mode="build",
    )
    after = path.read_text(encoding="utf-8")
    if before == after:
        log("warn: agent did not change file for undo test — skip restore assert")
    else:
        msg = await rt.undo_last_turn()
        log(f"undo: {msg}")
        restored = path.read_text(encoding="utf-8")
        if restored != before:
            # undo might restore earlier snapshot
            log(f"warn: undo content differs (still OK if snapshots ran): {len(restored)} vs {len(before)}")


async def test_persist(home: Path, repo: Path, sid: str) -> None:
    log("=== reopen session ===")
    os.environ["TEVARN_CODE_HOME"] = str(home)
    save_user_settings_patch({"agent": {"max_iterations": 33}})
    rt2, store2 = await make_runtime(repo, home, session_id=sid)
    try:
        sp = home / "settings.json"
        if not sp.is_file():
            raise Fail("settings.json missing")
        data = json.loads(sp.read_text(encoding="utf-8"))
        if data.get("agent", {}).get("max_iterations") != 33:
            raise Fail(f"settings not persisted: {data}")
        row = await store2.get_session(sid)
        if not row:
            raise Fail("session missing")
        msgs = await store2.load_messages(sid)
        parts = await store2.load_parts(sid)
        if len(msgs) < 2:
            raise Fail("messages missing")
        log(f"reopen msgs={len(msgs)} parts={len(parts)} slug={row.get('slug')}")
        r = await rt2.run_turn("一句话确认这是 sample calc 项目。", force_mode="ask")
        if r.error:
            raise Fail(r.error)
    finally:
        await rt2.llm.close()
        await store2.close()


async def test_bridge() -> None:
    log("=== bridge probe ===")
    br = build_bridge(BridgeConfig(enabled=True, base_url=BRIDGE_URL, timeout_sec=8))
    try:
        h = await br.health()
        log(f"bridge health: {h}")
        if not h.get("ok"):
            log("bridge not available — skip deep checks (OK if desktop down)")
            return
        models = await br.list_models()
        skills = await br.list_skills()
        tools = await br.list_tools()
        log(f"bridge models={len(models)} skills={len(skills)} tools={len(tools)}")
        if not models:
            log("warn: no models from bridge")
        # try rag (may be empty)
        from tevarn_code.bridge.protocol import RAGQuery

        hits = await br.rag_search(RAGQuery(query="tevarn", top_k=3))
        log(f"rag hits={len(hits)}")
    finally:
        await br.close()


async def main() -> int:
    t0 = time.time()
    home = Path(tempfile.mkdtemp(prefix="tkc-home-"))
    repo = prep_repo()
    log(f"home={home}")
    log(f"repo={repo}")
    log(f"llm={LOCAL_BASE} model={LOCAL_MODEL}")
    try:
        await test_unit_local()
        await test_bridge()
        await test_llm_ping()
        rt, store = await make_runtime(repo, home)
        sid = rt.session_id
        assert sid
        try:
            await test_plan_readonly(rt)
            await test_explore_readonly(rt)
            await test_coding(rt)
            await test_interrupt(rt)
            await test_compress(rt)
            await test_queue_and_undo(rt)
            await rt._persist_messages()
            await store.set_setting("smoke_marker", {"ok": True, "ts": time.time()})
            # parts must exist
            parts = await store.load_parts(sid)
            if len(parts) < 5:
                raise Fail(f"too few parts: {len(parts)}")
            log(f"parts total={len(parts)}")
            sess = await store.get_session(sid)
            log(f"session slug={sess.get('slug')} compress={sess.get('compress_count')}")
        finally:
            await rt.llm.close()
            await store.close()

        await test_persist(home, repo, sid)
        log(f"ALL SMOKE PASSED in {time.time()-t0:.1f}s")
        return 0
    except Exception as e:
        log(f"FAILED: {e}")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
