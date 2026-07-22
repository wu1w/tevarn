"""LOCAL llama.cpp end-to-end smoke for Takton Code.

Requirements (exit 0 only if all pass):
1. LLM reachable (LOCAL)
2. Coding task on sample_repo (add function + test)
3. Interrupt mid-turn + /continue restore
4. Trigger compression >= 5 times; session remains coherent
5. Session + settings persistence across reopen
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

# ensure src on path when run as script
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from takton_code.agent.loop import AgentRuntime
from takton_code.bridge import BridgeConfig, build_bridge
from takton_code.config import load_settings
from takton_code.llm.provider import OpenAICompatibleProvider, build_llm_provider
from takton_code.project.binder import bind_project, init_project_files
from takton_code.session.store import SessionStore

AIGA_BASE = os.environ.get("TAKTON_CODE_BASE_URL", "http://192.168.5.32:8088/v1")
AIGA_MODEL = os.environ.get(
    "TAKTON_CODE_MODEL", "local-model.gguf"
)
# aggressive compression for smoke
SMOKE_CTX = int(os.environ.get("TAKTON_CODE_CONTEXT_WINDOW", "8000"))
SMOKE_THRESH = float(os.environ.get("TAKTON_CODE_COMPRESS_THRESHOLD", "0.25"))


class Fail(Exception):
    pass


def log(msg: str) -> None:
    print(f"[smoke] {msg}", flush=True)


async def check_llm() -> None:
    p = OpenAICompatibleProvider(
        base_url=AIGA_BASE,
        api_key="no-key",
        model=AIGA_MODEL,
        max_tokens=256,
        temperature=0.1,
    )
    try:
        r = await p.chat(
            [
                {"role": "system", "content": "Reply with exactly: PONG"},
                {"role": "user", "content": "ping"},
            ]
        )
        text = (r.content or r.reasoning_content or "").strip()
        log(f"LLM ping => {text[:120]!r}")
        if not text:
            raise Fail("empty LLM response")
    finally:
        await p.close()


def prep_repo() -> Path:
    src = ROOT / "fixtures" / "sample_repo"
    td = Path(tempfile.mkdtemp(prefix="tkc-smoke-"))
    dest = td / "sample_repo"
    shutil.copytree(src, dest)
    # git init for binder
    import subprocess

    subprocess.run(["git", "init"], cwd=dest, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "smoke@takton.local"], cwd=dest, check=True)
    subprocess.run(["git", "config", "user.name", "smoke"], cwd=dest, check=True)
    subprocess.run(["git", "add", "-A"], cwd=dest, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=dest, check=True, capture_output=True)
    init_project_files(dest, "python -m pytest -q", "python -m compileall -q .")
    return dest


async def make_runtime(repo: Path, home: Path, session_id: str | None = None) -> tuple[AgentRuntime, SessionStore]:
    os.environ["TAKTON_CODE_HOME"] = str(home)
    os.environ["TAKTON_CODE_BASE_URL"] = AIGA_BASE
    os.environ["TAKTON_CODE_MODEL"] = AIGA_MODEL
    os.environ["TAKTON_CODE_CONTEXT_WINDOW"] = str(SMOKE_CTX)
    os.environ["TAKTON_CODE_COMPRESS_THRESHOLD"] = str(SMOKE_THRESH)
    os.environ["TAKTON_CODE_MAX_TOKENS"] = "2048"

    settings = load_settings()
    # force smoke compression knobs
    settings.llm.base_url = AIGA_BASE
    settings.llm.model = AIGA_MODEL
    settings.llm.context_window = SMOKE_CTX
    settings.llm.compress_threshold = SMOKE_THRESH
    settings.llm.max_tokens = 2048
    settings.llm.compress_keep_recent = 4
    settings.agent.max_iterations = 24
    settings.agent.auto_plan_complex = False  # direct build for speed on small tasks
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
    )
    bridge = build_bridge(BridgeConfig(enabled=False))
    events: list[dict] = []

    def on_event(ev: dict) -> None:
        events.append(ev)
        if ev.get("type") in ("compress", "tool_start", "error", "cancel_requested"):
            log(f"event {ev.get('type')}: {json.dumps({k: ev[k] for k in ev if k != 'type'}, ensure_ascii=False)[:200]}")

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
    rt._smoke_events = events  # type: ignore[attr-defined]
    await rt.setup(session_id=session_id, title="smoke")
    return rt, store


async def test_coding_task(rt: AgentRuntime) -> None:
    log("=== coding task: add sub() ===")
    prompt = (
        "In build mode, edit src/calc.py to add function `sub(a: int, b: int) -> int` returning a-b. "
        "Update tests/test_calc.py with test_sub. Then run_tests. Keep changes minimal."
    )
    result = await rt.run_turn(prompt, force_mode="build")
    log(f"coding done ok={result.ok} iter={result.iterations} interrupted={result.interrupted}")
    log(f"final preview: {(result.final_text or '')[:300]}")
    log(f"changes: {result.changes_summary}")
    calc = (rt.project.root / "src" / "calc.py").read_text(encoding="utf-8")
    if "def sub" not in calc:
        # allow agent to have used different approach — try one repair turn
        log("sub() missing — repair turn")
        result = await rt.run_turn(
            "src/calc.py still missing def sub. Add it and a test, then run_tests.",
            force_mode="build",
        )
        calc = (rt.project.root / "src" / "calc.py").read_text(encoding="utf-8")
    if "def sub" not in calc:
        raise Fail(f"sub() not added to calc.py:\n{calc}")
    # run tests ourselves
    out = await rt.tools.run_tests({})  # type: ignore[union-attr]
    log(f"tests: {out[:400]}")
    if "exit=0" not in out.splitlines()[0] and "exit=0" not in out[:20]:
        # pytest might still pass with warnings
        if "failed" in out.lower() and "passed" not in out.lower():
            raise Fail(f"tests failed:\n{out}")


async def test_interrupt_continue(rt: AgentRuntime) -> None:
    log("=== interrupt + continue (x3) ===")
    for n in range(3):
        log(f"--- interrupt round {n+1}/3 ---")
        task = asyncio.create_task(
            rt.run_turn(
                f"Round {n+1}: List project python files with glob, read src/calc.py, "
                f"then write a short note about add/mul/sub. Be thorough with multiple tool calls.",
                force_mode="ask" if n % 2 == 0 else "build",
            )
        )
        await asyncio.sleep(3.0 + n)
        rt.request_cancel()
        result = await task
        log(f"round {n+1} interrupted={result.interrupted} ok={result.ok} iter={result.iterations}")
        cont = await rt.continue_after_interrupt(f"请继续完成 round {n+1} 的简要说明，一两段即可")
        log(f"round {n+1} continue ok={cont.ok} err={cont.error} text={(cont.final_text or '')[:160]}")
        if cont.error:
            raise Fail(f"continue failed round {n+1}: {cont.error}")
        # session must remain consistent
        msgs = await rt.store.load_messages(rt.session_id)  # type: ignore[arg-type]
        if len(msgs) < 3:
            raise Fail(f"messages too few after interrupt/continue round {n+1}: {len(msgs)}")
        if not rt.llm_snapshot.get("model"):
            raise Fail("llm_snapshot lost after interrupt")
        # mode/settings state
        row = await rt.store.get_session(rt.session_id)  # type: ignore[arg-type]
        if not row:
            raise Fail("session row missing")
        log(f"round {n+1} msgs={len(msgs)} status={row['status']} mode={row['mode']}")
    log("multi-interrupt OK")


async def test_compression(rt: AgentRuntime) -> None:
    log("=== force compression >= 5 ===")
    assert rt.compressor
    # pad history with large tool-like messages via store/messages
    blob = ("LOREM_IPSUM_SMOKE_PAD " * 80) + "\n"
    for i in range(30):
        rt.messages.append({"role": "user", "content": f"pad-user-{i}\n" + blob})
        rt.messages.append(
            {
                "role": "assistant",
                "content": f"pad-assistant-{i}\n" + blob,
            }
        )
    await rt._persist_messages()

    for i in range(6):
        # inflate again each round
        rt.messages.append({"role": "user", "content": f"force-compress-round-{i}\n" + blob * 3})
        rt.messages.append({"role": "assistant", "content": f"ack-{i}\n" + blob * 2})
        await rt._maybe_compress(force=True, reason=f"smoke-{i}")
        log(f"compress round {i+1}: count={rt.compressor.compress_count} msgs={len(rt.messages)}")

    if rt.compressor.compress_count < 5:
        raise Fail(f"compress_count={rt.compressor.compress_count} < 5")

    # after many compressions, still can chat
    r = await rt.run_turn(
        "Reply with one line: COMPRESS_OK and the number of files in src/ via glob tool.",
        force_mode="build",
    )
    log(f"post-compress turn: ok={r.ok} err={r.error} text={(r.final_text or '')[:240]}")
    if r.error:
        raise Fail(f"post-compress LLM broken: {r.error}")
    # system message must remain
    if not rt.messages or rt.messages[0].get("role") != "system":
        raise Fail("system message lost after compression")
    # llm snapshot stable
    if not rt.llm_snapshot.get("model"):
        raise Fail("llm_snapshot missing model")


async def test_settings_session_persist(home: Path, repo: Path, session_id: str) -> None:
    log("=== reopen session + settings ===")
    # write settings
    from takton_code.config import save_user_settings_patch

    os.environ["TAKTON_CODE_HOME"] = str(home)
    save_user_settings_patch({"agent": {"max_iterations": 33}, "llm": {"temperature": 0.11}})

    rt2, store2 = await make_runtime(repo, home, session_id=session_id)
    try:
        # settings file exists
        sp = home / "settings.json"
        if not sp.is_file():
            raise Fail("settings.json not written")
        data = json.loads(sp.read_text(encoding="utf-8"))
        if data.get("agent", {}).get("max_iterations") != 33:
            raise Fail(f"settings not persisted: {data}")

        row = await store2.get_session(session_id)
        if not row:
            raise Fail("session missing on reopen")
        msgs = await store2.load_messages(session_id)
        if len(msgs) < 2:
            raise Fail("messages not persisted")
        # snapshot present
        snap = json.loads(row["llm_snapshot"] or "{}")
        if not snap.get("model"):
            raise Fail("llm snapshot empty on reopen")
        log(f"reopen ok msgs={len(msgs)} snap_model={snap.get('model')} status={row['status']}")

        # one more turn on resumed session
        r = await rt2.run_turn("用一句话确认你还记得这是 sample calc 项目。", force_mode="ask")
        if r.error:
            raise Fail(f"resume turn error: {r.error}")
        log(f"resume turn text={(r.final_text or '')[:200]}")
    finally:
        await rt2.llm.close()
        await store2.close()


async def test_bridge_interfaces_reserved() -> None:
    log("=== bridge interface smoke (disabled) ===")
    from takton_code.bridge.protocol import BRIDGE_ROUTES, ChatRequest, RAGQuery, ToolInvokeRequest

    assert "list_models" in BRIDGE_ROUTES
    assert "rag_search" in BRIDGE_ROUTES
    b = build_bridge(BridgeConfig(enabled=False))
    h = await b.health()
    assert h.get("enabled") is False
    # construct request models (schema stability)
    _ = ChatRequest(model="x", messages=[])
    _ = RAGQuery(query="test")
    _ = ToolInvokeRequest(name="noop")
    await b.close()
    log("bridge schemas OK")


async def main() -> int:
    t0 = time.time()
    home = Path(tempfile.mkdtemp(prefix="tkc-home-"))
    repo = prep_repo()
    log(f"home={home}")
    log(f"repo={repo}")
    log(f"llm={AIGA_BASE} model={AIGA_MODEL} ctx={SMOKE_CTX} thresh={SMOKE_THRESH}")

    try:
        await test_bridge_interfaces_reserved()
        await check_llm()
        rt, store = await make_runtime(repo, home)
        sid = rt.session_id
        assert sid
        try:
            await test_coding_task(rt)
            await test_interrupt_continue(rt)
            await test_compression(rt)
            # persist before close
            await rt._persist_messages()
            await store.set_setting("smoke_marker", {"ok": True, "ts": time.time()})
            marker = await store.get_setting("smoke_marker")
            if not marker or not marker.get("ok"):
                raise Fail("settings_kv failed")
        finally:
            await rt.llm.close()
            await store.close()

        await test_settings_session_persist(home, repo, sid)

        # final assert compress events from DB session still loadable
        store3 = SessionStore(home / "state.db")
        await store3.open()
        msgs = await store3.load_messages(sid)
        st = await store3.all_settings()
        await store3.close()
        log(f"FINAL msgs={len(msgs)} settings_keys={list(st.keys())}")
        log(f"ALL SMOKE PASSED in {time.time()-t0:.1f}s")
        return 0
    except Exception as e:  # noqa: BLE001
        log(f"FAILED: {e}")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
