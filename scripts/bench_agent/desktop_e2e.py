#!/usr/bin/env python3
"""Desktop pack E2E on Linux (Xvfb-friendly).

Usage:
  export DISPLAY=:99   # or start Xvfb first
  cd /opt/hermes-workspace/takton
  .venv311/bin/python scripts/bench_agent/desktop_e2e.py

Optional agent loop with LLM:
  set -a; source /opt/hermes-workspace/.secrets/bench_llm.env; set +a
  .venv311/bin/python scripts/bench_agent/desktop_e2e.py --with-agent --model local
"""
from __future__ import annotations

import argparse
import re
import asyncio
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


@dataclass
class StepResult:
    name: str
    ok: bool
    detail: str = ""
    ms: float = 0.0


@dataclass
class E2EReport:
    display: str
    steps: list[StepResult] = field(default_factory=list)
    passed: int = 0
    failed: int = 0

    def add(self, step: StepResult) -> None:
        self.steps.append(step)
        if step.ok:
            self.passed += 1
        else:
            self.failed += 1


def ensure_display(display: str) -> str:
    os.environ["DISPLAY"] = display
    # optional MIT-MAGIC-COOKIE for real seat (gdm/user)
    xauth = os.environ.get("XAUTHORITY") or os.environ.get("TAKTON_XAUTHORITY")
    if xauth and Path(xauth).is_file():
        os.environ["XAUTHORITY"] = xauth
    env = {**os.environ, "DISPLAY": display}
    if os.environ.get("XAUTHORITY"):
        env["XAUTHORITY"] = os.environ["XAUTHORITY"]
    r = subprocess.run(
        ["xdpyinfo"],
        env=env,
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        raise SystemExit(
            f"Cannot open DISPLAY={display}. Start: Xvfb {display} -screen 0 1280x720x24 -ac &\n"
            f"stderr={r.stderr[:200]}"
        )
    return display


def spawn_fixture_window() -> subprocess.Popen:
    """Open a simple window with known title for click/type targets."""
    # xmessage blocks until closed — run in background
    env = {**os.environ}
    # geometry + title
    proc = subprocess.Popen(
        [
            "xmessage",
            "-center",
            "-buttons",
            "OK:0",
            "-default",
            "OK",
            "-timeout",
            "120",
            "TAKTON_DESKTOP_E2E_FIXTURE",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.8)
    return proc


async def run_tool(name: str, **kwargs):
    from backend.tools.registry import ToolRegistry

    return await ToolRegistry.execute(name, kwargs)


def parse_tool_result(res) -> dict:
    """ToolRegistry 可能返回 JSON str 或 dict。"""
    if isinstance(res, dict):
        return res
    s = str(res)
    try:
        import json
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    success = ("'success': True" in s) or ('"success": true' in s.lower()) or ("success=True" in s)
    data = {}
    try:
        import ast
        obj = ast.literal_eval(s)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    if "截图成功" in s or "点击成功" in s or "输入成功" in s or "滚动成功" in s or "已打开" in s:
        success = True
    return {"success": success, "message": s[:300], "data": data, "raw": s}


async def step_screenshot(report: E2EReport, out_dir: Path) -> dict:
    t0 = time.time()
    res = await run_tool("desktop_screenshot")
    ms = (time.time() - t0) * 1000
    parsed = parse_tool_result(res)
    ok = bool(parsed.get("success"))
    detail = str(parsed.get("message") or "")[:200]
    data = parsed.get("data") or {}
    shot_path = data.get("path") if isinstance(data, dict) else None
    img_b64 = data.get("image") if isinstance(data, dict) else None
    nbytes = int((data or {}).get("bytes") or 0) if isinstance(data, dict) else 0
    if shot_path and Path(shot_path).is_file():
        import shutil
        dest = out_dir / "screenshot.jpg"
        shutil.copy2(shot_path, dest)
        detail = f"{detail}; path={shot_path} bytes={Path(shot_path).stat().st_size}"
        ok = ok and Path(shot_path).stat().st_size > 1000
    elif img_b64:
        import base64
        raw = base64.b64decode(img_b64)
        path = out_dir / "screenshot.jpg"
        path.write_bytes(raw)
        detail = f"{detail}; saved {path} ({len(raw)} bytes)"
        ok = ok and len(raw) > 1000
    elif ok and nbytes > 1000:
        detail = f"{detail}; bytes={nbytes}"
    elif ok:
        ok = False
        detail = detail + "; empty screenshot payload"
    report.add(StepResult("desktop_screenshot", ok, detail, ms))
    return data if isinstance(data, dict) else {}


async def step_open_app(report: E2EReport) -> None:
    t0 = time.time()
    # xclock is always available on this host
    res = await run_tool("desktop_open_app", app_name="xclock")
    ms = (time.time() - t0) * 1000
    ok = bool(parse_tool_result(res).get("success"))
    report.add(StepResult("desktop_open_app", ok, str(res)[:200], ms))
    await asyncio.sleep(0.5)


def _screen_center() -> tuple[int, int]:
    try:
        env = {**os.environ}
        r = subprocess.run(["xdpyinfo"], env=env, capture_output=True, text=True)
        m = re.search(r"dimensions:\s+(\d+)x(\d+)", r.stdout or "")
        if m:
            return int(m.group(1)) // 2, int(m.group(2)) // 2
    except Exception:
        pass
    return 640, 360


async def step_click_center(report: E2EReport) -> None:
    t0 = time.time()
    cx, cy = _screen_center()
    res = await run_tool("desktop_click", x=cx, y=cy)
    ms = (time.time() - t0) * 1000
    ok = bool(parse_tool_result(res).get("success"))
    report.add(StepResult("desktop_click", ok, str(res)[:200], ms))


async def step_type(report: E2EReport) -> None:
    t0 = time.time()
    res = await run_tool("desktop_type", text="takton_e2e")
    ms = (time.time() - t0) * 1000
    ok = bool(parse_tool_result(res).get("success"))
    report.add(StepResult("desktop_type", ok, str(res)[:200], ms))


async def step_scroll(report: E2EReport) -> None:
    t0 = time.time()
    res = await run_tool("desktop_scroll", direction="down", amount=2)
    ms = (time.time() - t0) * 1000
    ok = bool(parse_tool_result(res).get("success"))
    report.add(StepResult("desktop_scroll", ok, str(res)[:200], ms))


async def step_pack_gate(report: E2EReport) -> None:
    """coding profile must not expose desktop_click until pack expand."""
    from backend.agent.tool_policy import merge_tools_with_packs, resolve_enabled_tool_names

    t0 = time.time()
    names, _ = resolve_enabled_tool_names(profile="coding", user_input="hi")
    blocked = names is not None and "desktop_click" not in names
    expanded = merge_tools_with_packs(names, ["desktop"])
    has = expanded is not None and "desktop_click" in expanded
    ok = blocked and has
    report.add(
        StepResult(
            "pack_gate_coding_vs_desktop",
            ok,
            f"coding_has_click={not blocked}; after_pack={has}",
            (time.time() - t0) * 1000,
        )
    )


async def step_agent_loop(report: E2EReport, model_alias: str) -> None:
    """Optional: LLM drives desktop_screenshot via tool loop."""
    from scripts.bench_agent.run_bench import CaseResult, chat, load_models
    from backend.tools.registry import ToolRegistry
    from backend.agent.pack_catalog import coding_plus_packs as cpacks

    try:
        models = load_models([model_alias])
    except SystemExit as e:
        report.add(StepResult("agent_desktop_screenshot", False, f"no credentials: {e}"))
        return
    cfg = models[0]
    names = cpacks("desktop")
    tools = ToolRegistry.get_tools_schema(names)
    sys_msg = (
        "You are Takton on a Linux desktop. DISPLAY is set. "
        "Call desktop_screenshot once, then briefly say success or failure in Chinese."
    )
    user = "请调用 desktop_screenshot 截取当前屏幕，然后用一句话说明是否成功。"
    messages = [
        {"role": "system", "content": sys_msg},
        {"role": "user", "content": user},
    ]
    t0 = time.time()
    tools_used: list[str] = []
    final = ""
    err = ""
    for _ in range(5):
        msg, usage, dt, err = await chat(cfg, messages, tools, max_tokens=400)
        if not msg:
            break
        tcs = msg.get("tool_calls") or []
        content = msg.get("content") or ""
        if not tcs:
            final = content
            break
        messages.append({"role": "assistant", "content": content or None, "tool_calls": tcs})
        for tc in tcs:
            fn = tc.get("function") or {}
            name = fn.get("name") or ""
            tools_used.append(name)
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {}
            result = await ToolRegistry.execute(name, args)
            # shrink huge base64 for next turn
            rtext = str(result)
            if len(rtext) > 2000:
                rtext = rtext[:500] + f"\n…[truncated tool result len={len(str(result))}]"
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.get("id"),
                    "name": name,
                    "content": rtext,
                }
            )
    ms = (time.time() - t0) * 1000
    ok = "desktop_screenshot" in tools_used and bool((final or tools_used))
    report.add(
        StepResult(
            "agent_desktop_screenshot",
            ok,
            f"tools={tools_used} final={final[:120]!r} err={err[:80]!r}",
            ms,
        )
    )


async def main_async(args: argparse.Namespace) -> int:
    from backend.tools.loader import load_all_tools

    display = ensure_display(args.display)
    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    report = E2EReport(display=display)
    print(f"DISPLAY={display}")

    await load_all_tools()
    await step_pack_gate(report)

    fixture = None
    try:
        fixture = spawn_fixture_window()
        await step_open_app(report)
        await step_screenshot(report, out_dir)
        await step_click_center(report)
        await step_type(report)
        await step_scroll(report)
        # second screenshot after interaction
        await step_screenshot(report, out_dir)
        if args.with_agent:
            await step_agent_loop(report, args.model)
    finally:
        if fixture and fixture.poll() is None:
            fixture.terminate()

    # write report
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload = {
        "ts": ts,
        "display": display,
        "passed": report.passed,
        "failed": report.failed,
        "steps": [asdict(s) for s in report.steps],
    }
    json_path = out_dir / f"desktop_e2e_{ts}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        f"# Desktop E2E {ts}",
        "",
        f"- DISPLAY: `{display}`",
        f"- passed: **{report.passed}** / failed: **{report.failed}**",
        "",
        "| step | ok | ms | detail |",
        "|------|----|----|--------|",
    ]
    for s in report.steps:
        det = s.detail.replace("|", "\\|")[:120]
        lines.append(f"| {s.name} | {'✅' if s.ok else '❌'} | {s.ms:.0f} | {det} |")
    md_path = out_dir / f"desktop_e2e_{ts}.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out_dir / "LATEST.md").write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")
    print("\n".join(lines))
    print(f"\nWrote {json_path}")
    print(f"Wrote {md_path}")
    return 0 if report.failed == 0 else 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--display", default=os.environ.get("DISPLAY") or ":99")
    ap.add_argument("--out", default="docs/bench/desktop_e2e")
    ap.add_argument("--with-agent", action="store_true")
    ap.add_argument("--model", default="local", help="local or kimi when --with-agent")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
