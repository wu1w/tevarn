#!/usr/bin/env python3
"""Overnight full user-flow test→fix loop (API + FE smoke + CEO gate/assign/budget).

Runs until --hours elapses (default 2h). On failure: attempt auto-repair, retest.
Writes live report to reports/overnight_user_flow.md and JSON summary.

Usage:
  .venv/Scripts/python.exe scripts/overnight_user_flow.py
  .venv/Scripts/python.exe scripts/overnight_user_flow.py --hours 2 --base http://127.0.0.1:8090
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONPATH", str(ROOT))
os.environ.setdefault("TEVARN_KERNEL_BACKEND", "rust")
os.environ.setdefault("TEVARN_KERNEL_AUTO_START", "1")

REPORT_MD = ROOT / "reports" / "overnight_user_flow.md"
REPORT_JSON = ROOT / "reports" / "overnight_user_flow.json"


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str) -> None:
    line = f"[{now()}] {msg}"
    print(line, flush=True)
    try:
        REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
        with REPORT_MD.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def http(
    method: str,
    url: str,
    token: str | None = None,
    body: dict | None = None,
    timeout: float = 30,
) -> tuple[int, object]:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw) if raw else {}
            except Exception:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw) if raw else {}
        except Exception:
            return e.code, raw
    except Exception as e:
        return 0, str(e)


class Result:
    def __init__(self) -> None:
        self.passed: list[str] = []
        self.failed: list[str] = []
        self.fixed: list[str] = []
        self.notes: list[str] = []

    def ok(self, name: str, detail: str = "") -> None:
        self.passed.append(name)
        log(f"  OK  {name}" + (f" — {detail}" if detail else ""))

    def fail(self, name: str, detail: str = "") -> None:
        self.failed.append(f"{name}: {detail}")
        log(f" FAIL {name}" + (f" — {detail}" if detail else ""))

    def note(self, msg: str) -> None:
        self.notes.append(msg)
        log(f" NOTE {msg}")


def ensure_host_alive() -> bool:
    """Restart host if ping fails."""
    try:
        from backend.kernel_rust.client import (
            is_rust_host_available,
            restart_kernel_host,
            start_kernel_host,
            DEFAULT_HOST,
        )
        import socket
        import json as _json

        if not is_rust_host_available():
            log("host port down — start_kernel_host")
            return bool(start_kernel_host())

        # raw ping
        host, _, port = DEFAULT_HOST.rpartition(":")
        s = socket.create_connection((host or "127.0.0.1", int(port or 17890)), 2)
        s.settimeout(3)
        s.sendall(b'{"jsonrpc":"2.0","id":1,"method":"ping","params":{}}\n')
        data = b""
        while b"\n" not in data:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
        s.close()
        if b"pong" in data or b"result" in data:
            return True
        log("host ping failed — hard restart")
        return bool(restart_kernel_host())
    except Exception as e:
        log(f"ensure_host_alive error: {e} — restart")
        try:
            from backend.kernel_rust.client import restart_kernel_host

            return bool(restart_kernel_host())
        except Exception as e2:
            log(f"restart failed: {e2}")
            return False


def restart_backend() -> bool:
    """Best-effort BE restart on Windows."""
    try:
        # kill uvicorn on 8090
        ps = (
            "Get-NetTCPConnection -LocalPort 8090 -State Listen -EA SilentlyContinue "
            "| Select-Object -ExpandProperty OwningProcess -Unique "
            "| ForEach-Object { Stop-Process -Id $_ -Force -EA SilentlyContinue }"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True,
            timeout=30,
        )
        time.sleep(2)
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)
        env["TEVARN_KERNEL_BACKEND"] = "rust"
        env["TEVARN_KERNEL_AUTO_START"] = "1"
        py = ROOT / ".venv" / "Scripts" / "python.exe"
        if not py.is_file():
            py = Path(sys.executable)
        subprocess.Popen(
            [
                str(py),
                "-m",
                "uvicorn",
                "backend.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8090",
                "--log-level",
                "info",
            ],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        for _ in range(40):
            st, body = http("GET", "http://127.0.0.1:8090/api/health", timeout=3)
            if st == 200:
                log("backend restarted OK")
                return True
            time.sleep(0.5)
        log("backend restart: health not ready")
        return False
    except Exception as e:
        log(f"restart_backend error: {e}")
        return False


def login(api: str) -> str | None:
    st, body = http("POST", f"{api}/auth/auto-login", body={})
    if st == 200 and isinstance(body, dict) and body.get("access_token"):
        return str(body["access_token"])
    st, body = http(
        "POST",
        f"{api}/auth/login",
        body={"email": "admin@tevarn.dev", "password": "admin"},
    )
    if st == 200 and isinstance(body, dict):
        return body.get("access_token")
    return None


def suite_api_core(api: str, fe: str, r: Result) -> None:
    st, health = http("GET", f"{api}/health")
    if st != 200:
        r.fail("health", str(health)[:160])
        return
    r.ok("health")

    token = login(api)
    if not token:
        r.fail("login", "no token")
        return
    r.ok("login")

    # Settings
    st, settings = http("GET", f"{api}/settings", token)
    r.ok("settings GET", f"st={st}") if st == 200 else r.fail("settings GET", str(settings)[:120])

    # Model catalog
    st, cat = http("GET", f"{api}/settings/model-catalog?fetch_models=false", token)
    if st == 404:
        st, cat = http("GET", f"{api}/model-catalog?fetch_models=false", token)
    (r.ok if st == 200 else r.fail)("model-catalog", f"st={st}")

    # Sessions
    st, sessions = http("GET", f"{api}/sessions/my", token)
    (r.ok if st == 200 else r.fail)(
        "sessions/my", f"n={len(sessions) if isinstance(sessions, list) else '?'}"
    )
    st, sess = http(
        "POST",
        f"{api}/sessions",
        token,
        body={"title": f"overnight-{int(time.time())}", "config": {"tool_profile": "coding"}},
    )
    sid = sess.get("id") if isinstance(sess, dict) else None
    (r.ok if st in (200, 201) and sid else r.fail)("session create", str(sid)[:36] if sid else str(sess)[:80])

    # Kernel panels
    for path in (
        "/kernel/runtime/health",
        "/kernel/processes",
        "/kernel/escalations?status=pending",
        "/kernel/project-groups",
        "/kernel/identities",
        "/kernel/processes/tree",
        "/kernel/governance/status",
        "/kernel/jobs/running",
    ):
        st, body = http("GET", f"{api}{path}", token, timeout=15)
        name = path.split("?")[0]
        if st == 200:
            r.ok(f"panel {name}")
        elif st == 404:
            r.note(f"panel {name} 404 (optional)")
        else:
            r.fail(f"panel {name}", f"st={st} {str(body)[:100]}")

    # Files
    st, finfo = http("GET", f"{api}/files/info", token)
    (r.ok if st == 200 else r.fail)("files/info", str(finfo)[:80] if st == 200 else str(finfo)[:120])

    # FE proxy (same paths via Next)
    for path in (
        "/api/health",
        "/api/kernel/processes",
        "/api/kernel/runtime/health",
        "/api/kernel/identities",
    ):
        st, body = http("GET", f"{fe}{path}", token, timeout=10)
        (r.ok if st == 200 else r.fail)(f"fe-proxy {path}", f"st={st}")


def suite_ceo_gate_assign_budget(api: str, r: Result) -> None:
    """CEO process mediate + crew_steward assign path + budget top-up."""
    import asyncio

    token = login(api)
    if not token:
        r.fail("ceo login", "no token")
        return

    # Hire/list employee
    st, idents_raw = http("GET", f"{api}/kernel/identities", token)
    idents = []
    if isinstance(idents_raw, dict):
        idents = idents_raw.get("identities") or []
    elif isinstance(idents_raw, list):
        idents = idents_raw
    active = [i for i in idents if isinstance(i, dict) and i.get("status") == "active"]
    emp = next(
        (i for i in active if str(i.get("name") or "").startswith("agent-engineer")),
        None,
    )
    if not emp and active:
        emp = active[0]
    if not emp:
        st, hire = http(
            "POST",
            f"{api}/kernel/identities",
            token,
            body={
                "name": f"overnight-emp-{int(time.time()) % 100000}",
                "role": "overnight",
                "capabilities": ["file_read", "file_rw", "command", "glob", "grep"],
                "default_token_budget": 200_000,
            },
        )
        if st in (200, 201) and isinstance(hire, dict):
            emp = hire
            r.ok("hire employee", emp.get("id", "")[:12])
        else:
            r.fail("hire employee", str(hire)[:160])
            return
    else:
        r.ok("use employee", f"{emp.get('name')} {str(emp.get('id'))[:8]}")

    # Budget top-up — try known routes
    iid = str(emp["id"])
    top_paths = [
        (
            "POST",
            f"/kernel/identities/{iid}/budget/top-up-running",
            {"amount": 50_000, "also_default": True, "reason": "overnight auto"},
        ),
        (
            "PATCH",
            f"/kernel/identities/{iid}",
            {
                "default_token_budget": int(emp.get("default_token_budget") or 200_000)
                + 50_000
            },
        ),
    ]
    topped = False
    for method, path, body in top_paths:
        st, top = http(method, f"{api}{path}", token, body=body)
        if st in (200, 201):
            r.ok("identity budget top-up", f"{method} {path} → {str(top)[:80]}")
            topped = True
            break
        r.note(f"budget try {method} {path} st={st}")
    if not topped:
        r.note("identity budget HTTP top-up skipped; process top_up in gate suite")

    # Inbox assign (CEO dispatch without LLM)
    st, enq = http(
        "POST",
        f"{api}/kernel/inbox",
        token,
        body={
            "identity_id": iid,
            "instruction": "overnight smoke: report capabilities and exit; no code changes",
            "source": "api",
            "priority": 5,
            "payload": {"token_budget": 80_000, "budget_source": "overnight", "via": "crew_steward"},
        },
    )
    (r.ok if st in (200, 201) else r.fail)(
        "inbox assign", f"st={st} {str(enq)[:140]}"
    )

    # Kernel gate: create CEO process + mediate crew_steward + file_read
    try:
        from backend.kernel import get_kernel
        from backend.kernel.tool_gate import enforce_tool_gate
        from backend.kernel_rust.client import reset_rust_kernel_for_tests

        # Ensure singleton can connect to live host
        try:
            reset_rust_kernel_for_tests()
        except Exception:
            pass

        async def gate_run():
            k = get_kernel()
            p = await k.create_process(
                "main",
                capabilities=[
                    "crew_steward",
                    "file_read",
                    "file_write",
                    "command",
                    "glob",
                    "grep",
                ],
                token_budget=300_000,
                meta={"coding_profile": "engineering", "session_id": "overnight"},
            )
            class CM:
                pass

            a1, e1 = await enforce_tool_gate(
                "file_read",
                {"path": "README.md", "_ws_manager": CM(), "_kernel_process_id": p.id},
                process_id=p.id,
            )
            a2, e2 = await enforce_tool_gate(
                "crew_steward",
                {
                    "action": "assign",
                    "name": emp.get("name"),
                    "instruction": "overnight gate assign smoke",
                    "token_budget": 60_000,
                    "_ws_manager": CM(),
                    "_kernel_process_id": p.id,
                },
                process_id=p.id,
            )
            # process-level budget top-up
            top_res = None
            if hasattr(k, "top_up_budget"):
                try:
                    top_res = k.top_up_budget(p.id, 25_000, by="overnight", reason="auto")
                except Exception as te:
                    top_res = {"error": str(te)}
            rem = getattr(p, "budget_remaining", None)
            soft = None
            if hasattr(k, "try_soft_renew_budget"):
                try:
                    soft = k.try_soft_renew_budget(p.id, need=1000, reason="overnight")
                except Exception as se:
                    soft = {"error": str(se)}
            # soft reconnect + mediate; on unknown process use atomic ensure
            try:
                k._soft_reconnect()
                d = await k.mediate(
                    p.id, "tool_call", "crew_steward", args={"action": "status"}
                )
                allowed = d.allowed
            except Exception as me:
                if "未知进程" in str(me) or "not found" in str(me).lower():
                    if hasattr(k, "ensure_and_mediate"):
                        new_p, dec = await k.ensure_and_mediate(
                            p.id,
                            identity="main",
                            capabilities=[
                                "crew_steward",
                                "file_read",
                                "file_write",
                                "command",
                            ],
                            token_budget=300_000,
                            meta={"coding_profile": "engineering"},
                            session_id="overnight",
                            target="crew_steward",
                            args={"action": "status"},
                        )
                        p = new_p
                        allowed = dec.allowed
                    else:
                        raise
                else:
                    raise
            try:
                await k.end_process(p.id, state="completed", reason="overnight")
            except Exception:
                pass
            return e1, e2, allowed, soft, rem, top_res

        e1, e2, allowed, soft, rem, top_res = asyncio.run(gate_run())
        (r.ok if e1 is None else r.fail)("gate file_read", str(e1))
        (r.ok if e2 is None else r.fail)("gate crew_steward assign", str(e2))
        (r.ok if allowed else r.fail)("mediate after soft reconnect", f"allowed={allowed}")
        r.ok("budget probe", f"remaining={rem} top={str(top_res)[:60]} soft={str(soft)[:60]}")
    except Exception as e:
        r.fail("ceo gate suite", f"{e}\n{traceback.format_exc()[-400:]}")


def suite_long_context_compress(api: str, r: Result) -> None:
    """Exercise context compress endpoint with large synthetic history."""
    token = login(api)
    if not token:
        r.fail("compress login", "no token")
        return
    # Prefer dedicated smoke_test compress if present
    big_msgs = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": ("段落" + str(i) + " ") * 200}
        for i in range(40)
    ]
    st, body = http(
        "POST",
        f"{api}/smoke-test/context-compress",
        token,
        body={"messages": big_msgs},
        timeout=60,
    )
    if st == 404:
        st, body = http(
            "POST",
            f"{api}/context/compress",
            token,
            body={"messages": big_msgs},
            timeout=60,
        )
    if st == 200:
        r.ok("context compress", str(body)[:120] if not isinstance(body, dict) else f"keys={list(body)[:8]}")
    else:
        r.note(f"context compress st={st} (optional) {str(body)[:100]}")


def suite_marathon_cycles(r: Result, cycles: int = 8) -> None:
    """Short host marathon cycles (create/snapshot/mediate)."""
    import asyncio

    try:
        from backend.kernel_rust.client import (
            RustAgentKernel,
            is_rust_host_available,
            start_kernel_host,
            reset_rust_kernel_for_tests,
        )
        from backend.kernel_rust.abi_gate import check_required_abi

        if not is_rust_host_available():
            start_kernel_host()
        reset_rust_kernel_for_tests()
        k = RustAgentKernel(auto_start=True)
        methods = k.list_methods()
        abi = check_required_abi(methods)
        if not abi["ok"]:
            r.fail("marathon ABI", str(abi.get("missing")))
            return
        r.ok("marathon ABI", f"methods={len(methods)}")

        async def one(i: int) -> None:
            p = await k.create_process(
                f"marathon-{i}",
                capabilities=["file_read", "crew_steward"],
                token_budget=50_000,
            )
            d = await k.mediate(
                p.id, "tool_call", "crew_steward", args={"action": "status"}
            )
            if not d.allowed:
                raise RuntimeError(f"mediate deny {d.reason}")
            if hasattr(k, "process_snapshot") or hasattr(k, "_call"):
                try:
                    k._call("process_snapshot", {"process_id": p.id, "reason": "overnight"})
                except Exception:
                    pass
            await k.end_process(p.id, state="completed", reason="marathon")

        for i in range(cycles):
            asyncio.run(one(i))
        r.ok(f"marathon {cycles} cycles")
    except Exception as e:
        r.fail("marathon", f"{e}")


def suite_playwright(r: Result) -> None:
    """Optional FE e2e if playwright installed."""
    try:
        fe = ROOT / "frontend"
        cmd = [
            "npx",
            "playwright",
            "test",
            "e2e/product-spine-hire-dispatch.spec.ts",
            "e2e/smoke.spec.ts",
            "--reporter=line",
            "--timeout=60000",
        ]
        env = os.environ.copy()
        env["SMOKE_BASE_URL"] = "http://127.0.0.1:3000"
        env["SMOKE_API_URL"] = "http://127.0.0.1:8090"
        proc = subprocess.run(
            cmd,
            cwd=str(fe),
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
            shell=True,
        )
        out = (proc.stdout or "")[-1500:] + (proc.stderr or "")[-800:]
        if proc.returncode == 0:
            r.ok("playwright hire-dispatch+smoke")
        else:
            r.fail("playwright", f"rc={proc.returncode}\n{out[-600:]}")
    except FileNotFoundError:
        r.note("playwright not available")
    except Exception as e:
        r.fail("playwright", str(e)[:200])


def auto_repair(failed: list[str], r: Result) -> None:
    """Attempt automatic repairs based on failure signatures."""
    blob = "\n".join(failed).lower()
    if any(
        x in blob
        for x in (
            "abi",
            "0 methods",
            "host",
            "timeout",
            "closed connection",
            "10053",
            "10054",
            "gate",
            "connection",
        )
    ):
        log("repair: ensure_host_alive + soft BE if needed")
        if ensure_host_alive():
            r.fixed.append("host restart/start")
        st, _ = http("GET", "http://127.0.0.1:8090/api/health", timeout=5)
        if st != 200:
            if restart_backend():
                r.fixed.append("backend restart")
                ensure_host_alive()


def write_summary(cycle: int, r: Result, elapsed_h: float) -> None:
    summary = {
        "cycle": cycle,
        "time": now(),
        "elapsed_hours": round(elapsed_h, 3),
        "passed": len(r.passed),
        "failed": len(r.failed),
        "fixed": r.fixed,
        "failures": r.failed[:50],
        "notes": r.notes[:30],
    }
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    # append-friendly: rewrite full latest + keep history list
    history = []
    if REPORT_JSON.is_file():
        try:
            prev = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
            history = prev.get("history") or []
        except Exception:
            history = []
    history.append(summary)
    REPORT_JSON.write_text(
        json.dumps({"latest": summary, "history": history[-100:]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log(
        f"=== cycle {cycle} done: pass={len(r.passed)} fail={len(r.failed)} "
        f"fixed={r.fixed} elapsed_h={elapsed_h:.2f} ==="
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=2.0)
    ap.add_argument("--base", default="http://127.0.0.1:8090")
    ap.add_argument("--fe", default="http://127.0.0.1:3000")
    ap.add_argument("--marathon-cycles", type=int, default=8)
    ap.add_argument("--skip-playwright", action="store_true")
    args = ap.parse_args()

    api = args.base.rstrip("/") + "/api"
    fe = args.fe.rstrip("/")
    REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
    REPORT_MD.write_text(
        f"# Overnight user-flow\n\nStarted {now()} hours={args.hours}\n\n",
        encoding="utf-8",
    )

    deadline = time.time() + args.hours * 3600
    cycle = 0
    total_fail = 0
    t0 = time.time()

    log(f"START overnight hours={args.hours} api={api} fe={fe}")
    ensure_host_alive()

    while time.time() < deadline:
        cycle += 1
        r = Result()
        log(f"----- cycle {cycle} -----")
        try:
            ensure_host_alive()
            suite_api_core(api, fe, r)
            suite_ceo_gate_assign_budget(api, r)
            suite_long_context_compress(api, r)
            suite_marathon_cycles(r, cycles=args.marathon_cycles)
            if not args.skip_playwright and cycle % 3 == 1:
                suite_playwright(r)
        except Exception as e:
            r.fail("cycle exception", f"{e}\n{traceback.format_exc()[-500:]}")

        if r.failed:
            total_fail += len(r.failed)
            auto_repair(r.failed, r)
            # retest core after repair
            r2 = Result()
            try:
                ensure_host_alive()
                suite_api_core(api, fe, r2)
                suite_ceo_gate_assign_budget(api, r2)
                if not r2.failed:
                    r.fixed.append("retest core OK after repair")
                    log("retest core OK after repair")
                else:
                    log(f"retest still failing: {r2.failed[:5]}")
            except Exception as e:
                log(f"retest error: {e}")

        write_summary(cycle, r, (time.time() - t0) / 3600)
        # brief pause between cycles
        time.sleep(15 if r.failed else 30)

    log(f"DONE cycles={cycle} total_fail_events={total_fail}")
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
