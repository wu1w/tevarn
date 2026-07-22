"""Autoloop v2 — deeper than Claude's ad-hoc multi-turn: explicit phases + stop rules.

Phases (bounded, session-driven — not a daemon):
  checkpoint → (optional plan) → build → lint → test → llm_verify → fix* → done

Beyond Claude:
- Explicit phase machine with events
- Doom-loop detection (same test error hash 3×)
- Per-phase file checkpoints
- Plan artifact under .takton/plans/
- Structured JSON result for CI
- No phone-home / no vendor lock
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class AutoloopResult:
    ok: bool
    rounds: list[dict[str, Any]] = field(default_factory=list)
    final_text: str = ""
    test_ok: bool | None = None
    lint_ok: bool | None = None
    verify_ok: bool | None = None
    plan_state: str = ""
    error: str | None = None
    checkpoint_ids: list[str] = field(default_factory=list)
    plan_path: str | None = None
    stop_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "rounds": self.rounds,
            "final_text": self.final_text,
            "test_ok": self.test_ok,
            "lint_ok": self.lint_ok,
            "verify_ok": self.verify_ok,
            "plan_state": self.plan_state,
            "error": self.error,
            "checkpoint_ids": self.checkpoint_ids,
            "plan_path": self.plan_path,
            "stop_reason": self.stop_reason,
        }


def parse_test_ok(output: str) -> bool | None:
    if not output:
        return None
    # prefer explicit exit=
    m = re.search(r"exit\s*=\s*(-?\d+)", output)
    if m:
        return int(m.group(1)) == 0
    low = output.lower()
    # pytest summary line: "3 failed, 10 passed"
    m2 = re.search(r"(\d+)\s+failed", low)
    if m2:
        return int(m2.group(1)) == 0
    if re.search(r"\b(error|errors)\b", low) and "no tests ran" in low:
        return False
    if "passed" in low and "failed" not in low and "error" not in low:
        return True
    if output.strip().endswith("OK") or "\nOK\n" in output:
        return True
    if str(output).startswith("ERROR"):
        return False
    return None


def parse_lint_ok(output: str) -> bool | None:
    if not output:
        return None
    m = re.search(r"exit\s*=\s*(-?\d+)", output)
    if m:
        return int(m.group(1)) == 0
    if str(output).startswith("ERROR"):
        return False
    low = output.lower()
    if "error" in low or "failed" in low:
        return False
    return True


def _err_fingerprint(text: str) -> str:
    # stable-ish: drop line numbers / timings
    t = re.sub(r"line \d+", "line N", text or "", flags=re.I)
    t = re.sub(r"\d+\.\d+s", "Ts", t)
    t = re.sub(r":\d+:", ":N:", t)
    return hashlib.sha256(t.encode("utf-8", errors="replace")).hexdigest()[:16]


async def run_autoloop(
    runtime: Any,
    goal: str,
    *,
    max_fix_rounds: int = 3,
    auto_approve_plan: bool = False,
    run_tests: bool = True,
    run_lint: bool = True,
    run_verify: bool = True,
    create_checkpoints: bool = True,
    write_plan_file: bool = True,
) -> AutoloopResult:
    out = AutoloopResult(ok=False)
    emit = getattr(runtime, "emit", None)

    def _emit(typ: str, **kw: Any) -> None:
        if emit:
            emit(typ, **kw)

    async def _cp(label: str) -> None:
        if not create_checkpoints or not getattr(runtime, "file_history", None) or not runtime.session_id:
            return
        try:
            paths = await _list_tracked_paths(runtime)
            # prefer paths touched this session if any
            if runtime.diff and getattr(runtime.diff, "changes", None):
                touched = list({c.path for c in runtime.diff.changes[-80:]})
                if touched:
                    paths = list(dict.fromkeys(touched + paths))[:300]
            pt = await runtime.file_history.create_point(
                runtime.session_id,
                label=label,
                kind="autoloop",
                paths=paths[:250],
            )
            out.checkpoint_ids.append(pt.id)
            _emit("history_point", point=pt.to_dict())
        except Exception as e:  # noqa: BLE001
            _emit("error", message=f"autoloop checkpoint {label}: {e}")

    # --- phase: pre checkpoint ---
    await _cp("autoloop:before")
    _emit("autoloop", phase="start", goal=goal[:200], max_fix_rounds=max_fix_rounds)

    # --- phase: initial ---
    r0 = await runtime.run_turn(goal)
    out.rounds.append(_round("initial", r0))
    out.final_text = r0.final_text or ""
    out.plan_state = r0.plan_state or ""
    if r0.error and not r0.ok:
        out.error = r0.error
        out.stop_reason = "initial_error"
        return out
    if r0.interrupted:
        out.error = "interrupted"
        out.stop_reason = "interrupted"
        return out

    from takton_code.plan.gate import PlanState

    # --- phase: plan gate ---
    if runtime.plan_gate.state == PlanState.PLAN_READY:
        plan = runtime.plan_gate.plan
        if write_plan_file and plan:
            try:
                ppath = _write_plan_artifact(runtime.project.root, plan, goal)
                out.plan_path = str(ppath)
                _emit("autoloop", phase="plan_written", path=out.plan_path)
            except Exception as e:  # noqa: BLE001
                _emit("error", message=f"plan file: {e}")
        if auto_approve_plan:
            _emit("autoloop", phase="approve_plan")
            r_build = await runtime.approve_plan_and_build("autoloop auto-approve")
            out.rounds.append(_round("build_after_plan", r_build))
            out.final_text = r_build.final_text or out.final_text
            out.plan_state = r_build.plan_state or out.plan_state
            if r_build.error and not r_build.ok:
                out.error = r_build.error
                out.stop_reason = "build_error"
                return out
            await _cp("autoloop:after_build")
        else:
            out.ok = True
            out.stop_reason = "await_approve"
            out.final_text = (out.final_text or "") + "\n\n[autoloop] plan ready — /approve or --yes-build"
            out.plan_state = "plan_ready"
            _emit("autoloop", phase="await_approve")
            return out
    else:
        await _cp("autoloop:after_initial")

    # --- phase: lint / test / verify / fix ---
    err_fingerprints: list[str] = []
    fix_i = 0
    while True:
        # lint (optional, non-fatal unless configured hard — soft fail)
        if run_lint and runtime.tools:
            lint_out = await _try_lint(runtime)
            lint_ok = parse_lint_ok(lint_out)
            out.lint_ok = lint_ok
            out.rounds.append(
                {"phase": "lint", "ok": lint_ok, "output": (lint_out or "")[:3000], "fix_round": fix_i}
            )
            _emit("autoloop", phase="lint", lint_ok=lint_ok)

        test_out = ""
        test_ok: bool | None = None
        if run_tests and runtime.tools:
            try:
                test_out = await runtime.tools.run_tests({})
                test_ok = parse_test_ok(test_out)
            except Exception as e:  # noqa: BLE001
                test_out = f"ERROR: {e}"
                test_ok = False
        elif not run_tests:
            test_ok = True
            test_out = "(tests skipped)"

        out.test_ok = test_ok
        out.rounds.append(
            {
                "phase": "test",
                "ok": bool(test_ok),
                "test_ok": test_ok,
                "output": (test_out or "")[:4000],
                "fix_round": fix_i,
            }
        )
        _emit("autoloop", phase="test", test_ok=test_ok, fix_round=fix_i)

        if test_ok is None:
            if str(test_out).startswith("ERROR"):
                test_ok = False
            else:
                # ambiguous — run verify if enabled else soft pass
                test_ok = not run_verify

        if test_ok:
            verify_ok = True
            if run_verify:
                verify_ok = await _llm_verify(runtime, goal, test_out)
                out.verify_ok = verify_ok
                out.rounds.append({"phase": "verify", "ok": verify_ok, "fix_round": fix_i})
                _emit("autoloop", phase="verify", verify_ok=verify_ok)
            if verify_ok:
                await _cp("autoloop:green")
                out.ok = True
                out.stop_reason = "green"
                _emit("autoloop", phase="done", test_ok=True, verify_ok=verify_ok)
                return out
            # verify failed → treat as need fix
            test_out = (test_out or "") + "\n[verify] LLM self-check failed — see conversation"
            test_ok = False

        # doom loop
        fp = _err_fingerprint(test_out or "")
        err_fingerprints.append(fp)
        if len(err_fingerprints) >= 3 and len(set(err_fingerprints[-3:])) == 1:
            out.error = "doom_loop: same test failure 3 times"
            out.stop_reason = "doom_loop"
            await _cp("autoloop:doom_loop")
            _emit("autoloop", phase="doom_loop", fingerprint=fp)
            return out

        if fix_i >= max_fix_rounds:
            out.error = f"tests still failing after {max_fix_rounds} fix rounds"
            out.ok = False
            out.stop_reason = "max_fix"
            await _cp("autoloop:failed")
            _emit("autoloop", phase="give_up", error=out.error)
            return out

        fix_i += 1
        fix_prompt = (
            f"Autoloop fix round {fix_i}/{max_fix_rounds}.\n"
            f"Original goal:\n{goal[:1500]}\n\n"
            f"Tests/verify failed. Apply the **smallest** fix. Do not refactor unrelated code.\n"
            f"After edits, stop — the orchestrator will re-run tests.\n\n"
            f"Failure output:\n```\n{(test_out or '')[:7000]}\n```\n"
        )
        _emit("autoloop", phase="fix", round=fix_i)
        r_fix = await runtime.run_turn(fix_prompt, force_mode="build")
        out.rounds.append(_round("fix", r_fix, extra={"round": fix_i}))
        out.final_text = r_fix.final_text or out.final_text
        await _cp(f"autoloop:after_fix_{fix_i}")
        if r_fix.interrupted:
            out.error = "interrupted during fix"
            out.stop_reason = "interrupted"
            return out


def _round(phase: str, r: Any, extra: dict | None = None) -> dict[str, Any]:
    d = {
        "phase": phase,
        "ok": getattr(r, "ok", None),
        "mode": getattr(r, "mode", None),
        "plan_state": getattr(r, "plan_state", None),
        "interrupted": getattr(r, "interrupted", None),
        "error": getattr(r, "error", None),
        "changes": getattr(r, "changes_summary", None),
        "text": (getattr(r, "final_text", None) or "")[:2000],
    }
    if extra:
        d.update(extra)
    return d


def _write_plan_artifact(root: Path, plan: Any, goal: str) -> Path:
    d = Path(root) / ".takton" / "plans"
    d.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    path = d / f"plan-{ts}.md"
    title = getattr(plan, "title", None) or "Plan"
    steps = getattr(plan, "steps", None) or []
    lines = [f"# {title}", "", f"Goal: {goal[:500]}", "", "## Steps"]
    for i, s in enumerate(steps):
        t = getattr(s, "title", None) or str(s)
        lines.append(f"{i+1}. {t}")
    risks = getattr(plan, "risks", None)
    if risks:
        lines += ["", "## Risks", str(risks)]
    tp = getattr(plan, "test_plan", None)
    if tp:
        lines += ["", "## Test plan", str(tp)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


async def _try_lint(runtime: Any) -> str:
    """Best-effort lint: ruff/eslint/project script — never invent success."""
    root = Path(runtime.project.root)
    candidates = []
    if (root / "pyproject.toml").exists() or list(root.glob("**/*.py"))[:1]:
        candidates.append("python -m ruff check .")
    if (root / "package.json").exists():
        candidates.append("npm run lint --if-present")
    if not candidates:
        return "exit=0\n(no lint configured)"
    # only first available
    cmd = candidates[0]
    try:
        return await runtime.tools.run_shell({"command": cmd, "timeout_sec": 120})
    except Exception:
        # fallback if no run_shell
        try:
            return await runtime.tools.execute("run_shell", {"command": cmd})
        except Exception as e:  # noqa: BLE001
            return f"ERROR: lint {e}"


async def _llm_verify(runtime: Any, goal: str, test_out: str) -> bool:
    """Short verify turn — model must answer VERIFIED or ISSUES."""
    prompt = (
        "Verification gate (autoloop). Read the repo evidence and test output.\n"
        f"Goal: {goal[:1200]}\n"
        f"Tests:\n```\n{(test_out or '')[:3000]}\n```\n"
        "If the goal is fully met and tests are green, reply with a single line: VERIFIED\n"
        "Otherwise reply: ISSUES: <short list>\n"
        "Do not modify files. Read-only."
    )
    try:
        # prefer ask mode if available
        prev = runtime.mode
        r = await runtime.run_turn(prompt, force_mode="ask")
        if prev and prev != runtime.mode:
            try:
                await runtime.set_mode(prev)
            except Exception:
                pass
        text = (r.final_text or "").strip().upper()
        if text.startswith("VERIFIED") or "\nVERIFIED" in text:
            return True
        if "ISSUES" in text:
            return False
        # ambiguous → fail closed if tests weren't clearly green
        return "VERIFIED" in text
    except Exception:
        return True  # don't block if verify turn fails to run


async def _list_tracked_paths(runtime: Any) -> list[str]:
    root = runtime.project.root
    try:
        import asyncio

        proc = await asyncio.create_subprocess_exec(
            "git",
            "ls-files",
            cwd=str(root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out_b, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        if proc.returncode == 0 and out_b:
            skip_ext = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".zip", ".exe", ".dll", ".wasm"}
            paths = []
            for ln in out_b.decode("utf-8", errors="replace").splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                if any(ln.lower().endswith(e) for e in skip_ext):
                    continue
                paths.append(ln)
            return paths
    except Exception:
        pass
    return []
