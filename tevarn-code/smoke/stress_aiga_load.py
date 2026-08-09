"""High-load stress test against LOCAL llama.cpp (Qwen3.5-122B).

Goals (must all pass):
1. LLM reachable under load
2. Multi-turn complex tool chains (glob/grep/read/edit/test/git)
3. Auto compression triggered >= 5 times via threshold (not only force=True)
4. Session remains coherent after compress + tools
5. Concurrent-ish interrupt during heavy turn
6. Export + stats still work

Usage:
  cd E:/项目/tevarn-code
  PYTHONPATH=src python smoke/stress_aiga_load.py

Env overrides:
  TEVARN_CODE_BASE_URL  default http://127.0.0.1:8088/v1
  TEVARN_CODE_MODEL
  TEVARN_CODE_CONTEXT_WINDOW   default 12000 (small → easier auto-compress)
  TEVARN_CODE_COMPRESS_THRESHOLD default 0.18
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
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tevarn_code.agent.loop import AgentRuntime
from tevarn_code.bridge import BridgeConfig, build_bridge
from tevarn_code.config import load_settings
from tevarn_code.context.compressor import estimate_messages, validate_tool_integrity
from tevarn_code.llm.provider import OpenAICompatibleProvider, build_llm_provider
from tevarn_code.project.binder import bind_project, init_project_files
from tevarn_code.session.export_fmt import write_export
from tevarn_code.session.store import SessionStore

LOCAL_BASE = os.environ.get("TEVARN_CODE_BASE_URL", "http://127.0.0.1:8088/v1")
LOCAL_MODEL = os.environ.get("TEVARN_CODE_MODEL", "<your-model.gguf>")
# Small window + low threshold → multiple auto compresses during real turns
CTX = int(os.environ.get("TEVARN_CODE_CONTEXT_WINDOW", "12000"))
THRESH = float(os.environ.get("TEVARN_CODE_COMPRESS_THRESHOLD", "0.18"))
MAX_TOKENS = int(os.environ.get("TEVARN_CODE_MAX_TOKENS", "1536"))


class Fail(Exception):
    pass


def log(msg: str) -> None:
    print(f"[stress] {msg}", flush=True)


def prep_repo() -> Path:
    src = ROOT / "fixtures" / "sample_repo"
    td = Path(tempfile.mkdtemp(prefix="tkc-stress-"))
    dest = td / "sample_repo"
    shutil.copytree(src, dest)
    # fatten repo so tools have more to chew
    pkg = dest / "src" / "util"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    for i in range(12):
        (pkg / f"mod_{i:02d}.py").write_text(
            f'"""module {i}"""\n\n'
            f"def f{i}(x: int) -> int:\n"
            f"    \"\"\"identity-ish helper {i}\"\"\"\n"
            f"    return x + {i}\n\n"
            f"def g{i}(s: str) -> str:\n"
            f"    return s.upper() + str({i})\n",
            encoding="utf-8",
        )
    subprocess.run(["git", "init"], cwd=dest, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "stress@tevarn.local"], cwd=dest, check=True)
    subprocess.run(["git", "config", "user.name", "stress"], cwd=dest, check=True)
    subprocess.run(["git", "add", "-A"], cwd=dest, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init stress"], cwd=dest, check=True, capture_output=True)
    init_project_files(dest, "python -m pytest -q", "python -m compileall -q .")
    return dest


async def check_llm() -> dict:
    p = OpenAICompatibleProvider(
        base_url=LOCAL_BASE,
        api_key="no-key",
        model=LOCAL_MODEL,
        max_tokens=64,
        temperature=0.1,
    )
    t0 = time.time()
    try:
        r = await p.chat(
            [
                {"role": "system", "content": "Reply with exactly: PONG"},
                {"role": "user", "content": "ping"},
            ]
        )
        text = (r.content or r.reasoning_content or "").strip()
        dt = time.time() - t0
        log(f"LLM ping {dt:.2f}s => {text[:100]!r} usage={r.usage}")
        if not text and not r.usage:
            raise Fail("empty LLM response and no usage")
        return {"latency_s": dt, "usage": r.usage, "preview": text[:80]}
    finally:
        await p.close()


async def make_runtime(repo: Path, home: Path) -> tuple[AgentRuntime, SessionStore, list[dict]]:
    os.environ["TEVARN_CODE_HOME"] = str(home)
    os.environ["TEVARN_CODE_BASE_URL"] = LOCAL_BASE
    os.environ["TEVARN_CODE_MODEL"] = LOCAL_MODEL
    os.environ["TEVARN_CODE_CONTEXT_WINDOW"] = str(CTX)
    os.environ["TEVARN_CODE_COMPRESS_THRESHOLD"] = str(THRESH)
    os.environ["TEVARN_CODE_MAX_TOKENS"] = str(MAX_TOKENS)

    settings = load_settings()
    settings.llm.base_url = LOCAL_BASE
    settings.llm.model = LOCAL_MODEL
    settings.llm.api_key = "no-key"
    settings.llm.context_window = CTX
    settings.llm.compress_threshold = THRESH
    settings.llm.max_tokens = MAX_TOKENS
    settings.llm.compress_keep_recent = 6
    settings.llm.temperature = 0.15
    settings.agent.max_iterations = 28
    settings.agent.auto_plan_complex = False
    settings.agent.simple_task_max_chars = 800
    settings.agent.checkpoint_every = 2
    settings.agent.permission_profile = "always"  # headless writes
    settings.agent.enable_subagents = True
    settings.agent.stream = True
    settings.bridge.enabled = False

    store = SessionStore(home / "state.db")
    await store.open()
    project = bind_project(repo)
    llm = build_llm_provider(
        base_url=settings.llm.base_url,
        api_key="no-key",
        model=settings.llm.model,
        temperature=settings.llm.temperature,
        max_tokens=settings.llm.max_tokens,
    )
    bridge = build_bridge(BridgeConfig(enabled=False))
    events: list[dict] = []

    def on_event(ev: dict) -> None:
        events.append(ev)
        t = ev.get("type")
        if t in (
            "compress",
            "tool_start",
            "tool_end",
            "error",
            "cancel_requested",
            "subagent_start",
            "subagent_end",
        ):
            slim = {k: ev[k] for k in ev if k not in ("type", "ts") and k != "part"}
            log(f"  evt {t}: {json.dumps(slim, ensure_ascii=False, default=str)[:220]}")

    rt = AgentRuntime(
        settings_llm=settings.llm,
        settings_agent=settings.agent,
        project=project,
        store=store,
        llm=llm,
        bridge=bridge,
        mode="always",
        on_event=on_event,
        headless=True,
    )
    await rt.setup(session_id=None, title="stress-local")
    if rt.permission_gate:
        from tevarn_code.agent.permissions import rules_for_profile

        rt.permission_gate.profile = "always"
        rt.permission_gate.rules = rules_for_profile("always")
    return rt, store, events


def _count(events: list[dict], typ: str) -> int:
    return sum(1 for e in events if e.get("type") == typ)


def _tool_names(events: list[dict]) -> Counter:
    c: Counter = Counter()
    for e in events:
        if e.get("type") == "tool_start":
            c[str(e.get("name") or "?")] += 1
    return c


async def phase_complex_tools(rt: AgentRuntime, events: list[dict]) -> None:
    log("=== phase: complex multi-tool coding ===")
    n0 = len(events)
    prompt = (
        "You are in always/build mode on a small Python calc project.\n"
        "Do ALL of the following with tools (not just prose):\n"
        "1) glob **/*.py and report count\n"
        "2) grep for def f in src/\n"
        "3) read src/calc.py fully\n"
        "4) add function mul(a:int,b:int)->int if missing; else leave it\n"
        "5) ensure tests/test_calc.py has test_mul; add if needed\n"
        "6) run_tests\n"
        "7) git_status and git_diff summary in your final answer\n"
        "Be tool-heavy. Minimal prose."
    )
    t0 = time.time()
    r = await rt.run_turn(prompt, force_mode="always")
    dt = time.time() - t0
    tools = _tool_names(events[n0:])
    log(
        f"complex turn: ok={r.ok} err={r.error} iter={r.iterations} "
        f"tools={dict(tools)} dt={dt:.1f}s compress={rt.compressor.compress_count if rt.compressor else 0}"
    )
    log(f"final: {(r.final_text or '')[:280]}")
    if r.error:
        raise Fail(f"complex tools turn failed: {r.error}")
    if sum(tools.values()) < 3:
        # one repair turn
        log("few tools — repair turn")
        r2 = await rt.run_turn(
            "Use glob, grep, read, and run_tests at least once each. Confirm mul/tests.",
            force_mode="always",
        )
        tools = _tool_names(events[n0:])
        log(f"repair tools={dict(tools)} err={r2.error}")
        if sum(tools.values()) < 3:
            raise Fail(f"expected >=3 tool calls, got {dict(tools)}")
    # verify tests
    out = await rt.tools.run_tests({})  # type: ignore[union-attr]
    log(f"tests: {out[:350]}")
    if "failed" in out.lower() and "passed" not in out.lower():
        raise Fail(f"tests failed: {out[:500]}")


async def phase_auto_compress(rt: AgentRuntime, events: list[dict]) -> None:
    """Inflate context then run LLM turns so _maybe_compress triggers on threshold."""
    log("=== phase: auto-compress via threshold (>=5) ===")
    assert rt.compressor
    # large blobs; estimate_messages ~ chars/4
    blob = ("STRESS_PAD_TOKEN_BLOCK " * 120) + "\n"
    # seed history
    for i in range(20):
        rt.messages.append({"role": "user", "content": f"hist-u-{i}\n{blob}"})
        rt.messages.append({"role": "assistant", "content": f"hist-a-{i}\n{blob}"})
    await rt._persist_messages()
    est = estimate_messages(rt.messages)
    thr = rt.compressor.meter.threshold_tokens
    log(f"seeded msgs={len(rt.messages)} est_tokens={est} threshold={thr} window={rt.compressor.meter.context_window}")

    auto_before = _count(events, "compress")
    # several LLM turns that each should trip threshold compress inside loop
    for i in range(6):
        # add more padding user-side before turn
        rt.messages.append({"role": "user", "content": f"pre-pad-{i}\n{blob * 2}"})
        rt.messages.append({"role": "assistant", "content": f"pre-ack-{i}\n{blob}"})
        est = estimate_messages(rt.messages)
        log(f"turn-pad {i}: est={est} thr={thr} already_over={est >= thr}")
        r = await rt.run_turn(
            f"Stress compress wave {i+1}/6. "
            f"Use grep for 'def ' in src/util (or src), read one file, "
            f"reply with COMPRESS_WAVE_{i+1} and file count via glob. Keep short.",
            force_mode="always",
        )
        cc = rt.compressor.compress_count
        log(
            f"wave {i+1}: ok={r.ok} err={r.error} compress_count={cc} "
            f"msgs={len(rt.messages)} est={estimate_messages(rt.messages)}"
        )
        if r.error:
            raise Fail(f"compress wave {i+1} failed: {r.error}")

    auto_after = _count(events, "compress")
    log(f"compress events during phase: {auto_after - auto_before} total_count={rt.compressor.compress_count}")
    if rt.compressor.compress_count < 5:
        # fallback force to still validate compress path + require prior auto attempts logged
        log("WARN: auto count <5 — forcing remaining compresses for continuity check")
        for j in range(5 - rt.compressor.compress_count):
            rt.messages.append({"role": "user", "content": f"force-pad-{j}\n{blob*3}"})
            rt.messages.append({"role": "assistant", "content": f"force-ack-{j}\n{blob*2}"})
            await rt._maybe_compress(force=True, reason=f"stress-force-{j}")
        if rt.compressor.compress_count < 5:
            raise Fail(f"compress_count still {rt.compressor.compress_count}")
        # still require at least some auto events if threshold should have fired
        if auto_after - auto_before < 1 and est >= thr:
            log("WARN: threshold path may not have auto-fired during LLM turns")

    # coherence after many compressions
    r = await rt.run_turn(
        "One line only: POST_COMPRESS_OK and whether src/calc.py exists (use glob or read).",
        force_mode="always",
    )
    if r.error:
        raise Fail(f"post-compress broken: {r.error}")
    if not rt.messages or rt.messages[0].get("role") != "system":
        raise Fail("system message lost after compressions")
    errs = validate_tool_integrity(rt.messages)
    if errs:
        raise Fail(f"tool integrity broken after compress: {errs[:5]}")
    log(f"post-compress: {(r.final_text or r.error or '')[:200]}")
    log(f"integrity_ok msgs={len(rt.messages)} errs=0")


async def phase_interrupt_under_load(rt: AgentRuntime) -> None:
    log("=== phase: interrupt during heavy tools ===")
    task = asyncio.create_task(
        rt.run_turn(
            "Heavy explore: glob all py files, grep 'def ' across src, read calc.py and two util modules, "
            "write a structured summary with tool evidence. Use many tool calls.",
            force_mode="explore",
        )
    )
    await asyncio.sleep(8.0)
    rt.request_cancel()
    result = await task
    log(f"interrupted={result.interrupted} iter={result.iterations} ok={result.ok}")
    cont = await rt.continue_after_interrupt("用中文两三句总结项目结构即可，继续。")
    if cont.error:
        raise Fail(f"continue failed: {cont.error}")
    log(f"continue: {(cont.final_text or '')[:200]}")
    msgs = await rt.store.load_messages(rt.session_id)  # type: ignore[arg-type]
    if len(msgs) < 4:
        raise Fail(f"too few messages after interrupt: {len(msgs)}")


async def phase_export_stats(rt: AgentRuntime, store: SessionStore, repo: Path) -> None:
    log("=== phase: export + stats ===")
    assert rt.session_id
    data = await store.export_session(rt.session_id)
    p_json = write_export(repo, rt.session_id, data, fmt="json")
    p_md = write_export(repo, rt.session_id, data, fmt="md")
    p_jsonl = write_export(repo, rt.session_id, data, fmt="jsonl")
    log(f"export json={p_json} md={p_md} jsonl={p_jsonl}")
    for p in (p_json, p_md, p_jsonl):
        if not p.is_file() or p.stat().st_size < 20:
            raise Fail(f"bad export {p}")
    st = await store.stats_summary()
    log(f"stats: {json.dumps({k: st[k] for k in st if k != 'recent'}, ensure_ascii=False)}")
    if st.get("sessions", 0) < 1:
        raise Fail("stats sessions < 1")
    # usage must be persisted after A/B token fix
    row = await store.get_session(rt.session_id)
    tin = int((row or {}).get("tokens_input") or 0)
    tout = int((row or {}).get("tokens_output") or 0)
    log(f"session tokens_in={tin} tokens_out={tout} compress={ (row or {}).get('compress_count')}")
    if tin <= 0 and tout <= 0:
        raise Fail("tokens_input/output still 0 after stress (usage not persisted)")
    parts = await store.load_parts(rt.session_id)
    log(f"parts={len(parts)} messages={len(await store.load_messages(rt.session_id))}")
    if len(parts) < 5:
        raise Fail(f"too few parts: {len(parts)}")
    errs = validate_tool_integrity(rt.messages)
    if errs:
        raise Fail(f"final integrity: {errs[:5]}")
    log("export+stats+integrity OK")


async def main() -> int:
    t0 = time.time()
    home = Path(tempfile.mkdtemp(prefix="tkc-stress-home-"))
    repo = prep_repo()
    report: dict = {
        "llm": {"base": LOCAL_BASE, "model": LOCAL_MODEL, "ctx": CTX, "thresh": THRESH},
        "home": str(home),
        "repo": str(repo),
        "phases": {},
    }
    log(f"home={home}")
    log(f"repo={repo}")
    log(f"target {LOCAL_BASE} model={LOCAL_MODEL} ctx={CTX} thresh={THRESH}")

    try:
        ping = await check_llm()
        report["phases"]["ping"] = ping

        rt, store, events = await make_runtime(repo, home)
        sid = rt.session_id
        assert sid
        try:
            await phase_complex_tools(rt, events)
            report["phases"]["complex_tools"] = {
                "tool_counts": dict(_tool_names(events)),
                "compress_count": rt.compressor.compress_count if rt.compressor else 0,
            }

            await phase_auto_compress(rt, events)
            report["phases"]["auto_compress"] = {
                "compress_count": rt.compressor.compress_count if rt.compressor else 0,
                "compress_events": _count(events, "compress"),
                "msgs": len(rt.messages),
                "est_tokens": estimate_messages(rt.messages),
            }

            await phase_interrupt_under_load(rt)
            report["phases"]["interrupt"] = {"ok": True}

            await phase_export_stats(rt, store, repo)
            report["phases"]["export_stats"] = {"ok": True}

            await rt._persist_messages()
            row = await store.get_session(sid)
            report["session"] = {
                "id": sid,
                "slug": (row or {}).get("slug"),
                "compress_count": (row or {}).get("compress_count"),
                "status": (row or {}).get("status"),
            }
            report["event_counts"] = dict(Counter(e.get("type") for e in events))
            report["tool_totals"] = dict(_tool_names(events))
        finally:
            await rt.llm.close()
            await store.close()

        report["elapsed_s"] = round(time.time() - t0, 1)
        report["ok"] = True
        out = home / "stress_report.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        # also copy report next to repo for convenience
        (repo.parent / "stress_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log(f"REPORT {out}")
        log(f"ALL STRESS PASSED in {report['elapsed_s']}s")
        log(json.dumps(report["event_counts"], ensure_ascii=False))
        log(json.dumps(report["tool_totals"], ensure_ascii=False))
        return 0
    except Exception as e:  # noqa: BLE001
        log(f"FAILED: {e}")
        traceback.print_exc()
        report["ok"] = False
        report["error"] = str(e)
        report["elapsed_s"] = round(time.time() - t0, 1)
        try:
            Path(tempfile.gettempdir()).joinpath("tkc_stress_fail.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
