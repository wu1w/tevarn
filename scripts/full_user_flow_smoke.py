#!/usr/bin/env python3
"""Full user-flow API smoke (no browser): auth → sessions → settings → kernel → crew → chat gate.

Does not replace Playwright UI tests; catches backend regressions that block FE.
Exit 0 only if all steps pass.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

API = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8090") + "/api"
fails: list[str] = []


def req(method: str, path: str, token: str | None = None, body: dict | None = None) -> tuple[int, dict | list | str]:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = urllib.request.Request(f"{API}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
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


def ok(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  OK  {name}" + (f" — {detail}" if detail else ""))
    else:
        print(f" FAIL {name}" + (f" — {detail}" if detail else ""))
        fails.append(f"{name}: {detail}")


def main() -> int:
    print(f"=== Full user-flow API smoke → {API} ===")
    st, health = req("GET", "/health")
    ok("health", st == 200 and (health.get("status") == "ok" if isinstance(health, dict) else False), str(health)[:120])

    st, login = req("POST", "/auth/auto-login", body={})
    if st != 200 or not isinstance(login, dict) or not login.get("access_token"):
        st, login = req(
            "POST",
            "/auth/login",
            body={"email": "admin@tevarn.dev", "password": "admin"},
        )
    token = login.get("access_token") if isinstance(login, dict) else None
    ok("login", bool(token), f"status={st}")
    if not token:
        return 1

    st, me = req("GET", "/auth/me", token)
    ok("auth/me", st == 200, str(me)[:80] if not isinstance(me, dict) else me.get("email", ""))

    st, sessions = req("GET", "/sessions/my", token)
    ok("sessions/my", st == 200, f"n={len(sessions) if isinstance(sessions, list) else type(sessions)}")

    st, sess = req(
        "POST",
        "/sessions",
        token,
        body={"title": f"flow-smoke-{int(time.time())}", "config": {"tool_profile": "coding"}},
    )
    sid = sess.get("id") if isinstance(sess, dict) else None
    ok("sessions create", st in (200, 201) and bool(sid), str(sid)[:36])

    st, settings = req("GET", "/settings", token)
    ok("settings get", st == 200, f"keys={len(settings) if isinstance(settings, dict) else 0}")

    st, rt = req("GET", "/kernel/runtime/health", token)
    if st == 404:
        st, rt = req("GET", "/runtime/health", token)
    ok(
        "runtime health",
        st == 200 and isinstance(rt, dict),
        f"ok={rt.get('ok') if isinstance(rt, dict) else rt} sev={rt.get('severity') if isinstance(rt, dict) else ''}",
    )

    st, procs = req("GET", "/kernel/processes", token)
    ok("kernel processes", st == 200)

    st, idents = req("GET", "/kernel/identities", token)
    ok("identities list", st == 200, f"n={len(idents) if isinstance(idents, list) else idents}")

    name = f"flow-emp-{int(time.time()) % 100000}"
    st, hire = req(
        "POST",
        "/kernel/identities",
        token,
        body={
            "name": name,
            "role": "smoke",
            "capabilities": ["file_read", "glob", "grep"],
            "default_token_budget": 50_000,
            "persona": "test",
            "duty": "e2e",
        },
    )
    iid = hire.get("id") if isinstance(hire, dict) else None
    ok("identity hire", st in (200, 201) and bool(iid), str(iid)[:36])

    if iid:
        st, enq = req(
            "POST",
            "/kernel/inbox",
            token,
            body={
                "identity_id": iid,
                "instruction": "smoke: list your capabilities only",
                "source": "api",
                "priority": 10,
            },
        )
        ok("inbox enqueue", st in (200, 201), str(enq)[:120])

    # files multi-root
    st, finfo = req("GET", "/files/info", token)
    ok(
        "files/info multi-root",
        st == 200 and isinstance(finfo, dict) and "sandbox_root" in finfo,
        str(finfo.get("allowed_roots") or finfo.get("sandbox_root"))[:80],
    )

    # kernel mediate path via python client (gate regression)
    try:
        sys.path.insert(0, ".")
        import asyncio
        from backend.kernel import get_kernel
        from backend.kernel.tool_gate import enforce_tool_gate

        async def gate_check():
            k = get_kernel()
            p = await k.create_process(
                "flow_smoke",
                capabilities=["file_read", "crew_steward", "command"],
                token_budget=100_000,
            )
            class CM:
                pass
            a, err = await enforce_tool_gate(
                "file_read",
                {"path": "README.md", "_ws_manager": CM(), "_kernel_process_id": p.id},
                process_id=p.id,
            )
            a2, err2 = await enforce_tool_gate(
                "crew_steward",
                {"action": "list", "_ws_manager": CM(), "_kernel_process_id": p.id},
                process_id=p.id,
            )
            await k.end_process(p.id, state="completed")
            return err, err2

        err, err2 = asyncio.run(gate_check())
        ok("gate file_read no CM crash", err is None, str(err))
        ok("gate crew_steward allow", err2 is None, str(err2))
    except Exception as e:
        ok("gate client checks", False, str(e))

    print("---")
    if fails:
        print(f"FAILED {len(fails)} steps:")
        for f in fails:
            print(" ", f)
        return 1
    print("ALL API FLOW STEPS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
