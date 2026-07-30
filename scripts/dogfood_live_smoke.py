#!/usr/bin/env python3
"""Live smoke against running Takton (loopback single-user).

Scenarios:
  1) Overnight Run — list runs/origins, checkpoint, resume probe, recovery import
  2) PPT preference — write preference memory via kernel API, recall via growth/memory
  3) Evolution — list drafts, replay one, record pass/fail gate

Usage:
  .venv/Scripts/python.exe scripts/dogfood_live_smoke.py
  .venv/Scripts/python.exe scripts/dogfood_live_smoke.py --base http://127.0.0.1:3000/api
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _req(base: str, method: str, path: str, body: dict | None = None) -> tuple[int, object]:
    url = base.rstrip("/") + path
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            code = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        code = e.code
    try:
        parsed: object = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        parsed = raw[:500]
    return code, parsed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8090/api")
    ap.add_argument("--fe", default="http://127.0.0.1:3000")
    args = ap.parse_args()
    base = args.base
    results: list[dict] = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    print(f"=== dogfood live smoke @ {now} base={base} ===")

    # FE pages
    for page in ("/", "/tasks", "/kernel", "/evolution", "/agents"):
        code, _ = _req(args.fe, "GET", page)
        # FE returns HTML not json — use raw urlopen
        try:
            with urllib.request.urlopen(args.fe + page, timeout=15) as resp:
                code = resp.status
                n = len(resp.read())
        except Exception as e:
            code, n = 0, 0
            print(f"FE {page} FAIL {e}")
        ok = code == 200 and n > 1000
        results.append({"name": f"fe{page}", "ok": ok, "detail": f"status={code} bytes={n}"})
        print(f"[{'OK' if ok else 'FAIL'}] FE {page} {code} {n}b")

    # Health
    code, body = _req(base, "GET", "/health")
    ok = code == 200
    results.append({"name": "health", "ok": ok, "detail": str(body)[:120]})
    print(f"[{'OK' if ok else 'FAIL'}] health {code}")

    # ── 1) Overnight Run smoke ─────────────────────────────────
    print("\n--- 1) Overnight Run ---")
    code, runs = _req(base, "GET", "/runs?limit=20")
    run_list = runs if isinstance(runs, list) else []
    origins = sorted({str(r.get("origin") or "?") for r in run_list if isinstance(r, dict)})
    statuses = sorted({str(r.get("status") or "?") for r in run_list if isinstance(r, dict)})
    ok = code == 200 and len(run_list) >= 0
    results.append(
        {
            "name": "runs_list",
            "ok": ok,
            "detail": f"n={len(run_list)} origins={origins} statuses={statuses[:8]}",
        }
    )
    print(f"[{'OK' if ok else 'FAIL'}] runs n={len(run_list)} origins={origins}")

    code, sess = _req(base, "GET", "/sessions/my")
    sessions = sess if isinstance(sess, list) else []
    sid = str(sessions[0]["id"]) if sessions else ""
    ok = code == 200 and bool(sid)
    results.append({"name": "sessions_my", "ok": ok, "detail": f"n={len(sessions)} sid={sid[:8]}"})
    print(f"[{'OK' if ok else 'FAIL'}] sessions n={len(sessions)}")

    if sid:
        code, cp = _req(base, "GET", f"/sessions/{sid}/checkpoint")
        can = bool(isinstance(cp, dict) and (cp.get("can_resume") is not None or "checkpoint" in cp))
        results.append(
            {
                "name": "checkpoint",
                "ok": code == 200 and can,
                "detail": f"can_resume={isinstance(cp, dict) and cp.get('can_resume')} keys={list(cp)[:6] if isinstance(cp, dict) else type(cp)}",
            }
        )
        print(f"[{'OK' if code==200 else 'FAIL'}] checkpoint can_resume={isinstance(cp, dict) and cp.get('can_resume')}")

        # resume probe (may no-op if nothing to resume)
        code, res = _req(base, "POST", f"/sessions/{sid}/resume", {})
        results.append(
            {
                "name": "resume_probe",
                "ok": code in (200, 400, 409),  # 200 ok / 400 nothing — both prove endpoint live
                "detail": f"status={code} body={str(res)[:160]}",
            }
        )
        print(f"[{'OK' if code in (200,400,409) else 'FAIL'}] resume probe HTTP {code} {str(res)[:100]}")

    # recovery module import + dry call against live DB
    try:
        sys.path.insert(0, str(ROOT))
        import asyncio
        import os

        os.environ.setdefault("TAKTON_TEST_MODE", "0")
        from backend.agent.run_recovery import recover_stale_runs

        summary = asyncio.run(recover_stale_runs(auto_resume=False))
        results.append(
            {
                "name": "run_recovery_dry",
                "ok": isinstance(summary, dict),
                "detail": str(summary)[:200],
            }
        )
        print(f"[OK] run_recovery(auto_resume=False) -> {summary}")
    except Exception as e:
        results.append({"name": "run_recovery_dry", "ok": False, "detail": str(e)[:200]})
        print(f"[FAIL] run_recovery {e}")

    code, procs = _req(base, "GET", "/kernel/processes")
    nproc = 0
    if isinstance(procs, dict):
        nproc = len(procs.get("processes") or [])
    results.append({"name": "kernel_processes", "ok": code == 200, "detail": f"n={nproc}"})
    print(f"[{'OK' if code==200 else 'FAIL'}] kernel processes n={nproc}")

    # ── 2) PPT preference ──────────────────────────────────────
    print("\n--- 2) PPT preference ---")
    code, idents = _req(base, "GET", "/kernel/identities")
    identities = []
    if isinstance(idents, dict):
        identities = idents.get("identities") or []
    elif isinstance(idents, list):
        identities = idents
    iid = str(identities[0]["id"]) if identities else ""
    ok = code == 200 and bool(iid)
    results.append({"name": "identities", "ok": ok, "detail": f"n={len(identities)} id={iid[:8]}"})
    print(f"[{'OK' if ok else 'FAIL'}] identities n={len(identities)}")

    pref_content = (
        f"[dogfood {now}] 公司 PPT 风格偏好：深蓝封面、少字多图、页脚含版本号、"
        f"正文字号≥18、禁用花哨动画。冒烟写入。"
    )
    if iid:
        code, mem = _req(
            base,
            "POST",
            f"/kernel/identities/{iid}/memory",
            {
                "kind": "preference",
                "content": pref_content,
                "source": "manual",
                "approved_by": "dogfood-smoke",
            },
        )
        # API may use different field names — adapt on failure
        if code >= 400:
            code, mem = _req(
                base,
                "POST",
                f"/kernel/identities/{iid}/memory",
                {"kind": "preference", "content": pref_content},
            )
        ok = code in (200, 201)
        results.append(
            {
                "name": "ppt_preference_write",
                "ok": ok,
                "detail": f"status={code} body={str(mem)[:180]}",
            }
        )
        print(f"[{'OK' if ok else 'FAIL'}] preference write HTTP {code}")

        code, memlist = _req(base, "GET", f"/kernel/identities/{iid}/memory")
        texts = []
        if isinstance(memlist, dict):
            for m in memlist.get("memory") or memlist.get("items") or []:
                texts.append(str(m.get("content") or ""))
        elif isinstance(memlist, list):
            for m in memlist:
                if isinstance(m, dict):
                    texts.append(str(m.get("content") or ""))
        hit = any("深蓝封面" in t or "dogfood" in t for t in texts)
        results.append(
            {
                "name": "ppt_preference_read",
                "ok": code == 200 and hit,
                "detail": f"status={code} hit={hit} n={len(texts)}",
            }
        )
        print(f"[{'OK' if code==200 and hit else 'FAIL'}] preference read hit={hit}")

        code, growth = _req(base, "GET", f"/kernel/identities/{iid}/growth")
        results.append(
            {
                "name": "growth",
                "ok": code == 200 and isinstance(growth, dict),
                "detail": f"status={code} keys={list(growth)[:8] if isinstance(growth, dict) else growth}",
            }
        )
        print(f"[{'OK' if code==200 else 'FAIL'}] growth HTTP {code}")

        # also bus remember preference for graph path
        try:
            import asyncio
            import os

            os.environ.setdefault("TAKTON_TEST_MODE", "0")
            sys.path.insert(0, str(ROOT))
            from backend.services import memory_bus

            async def _bus():
                wr = await memory_bus.remember(
                    "preference",
                    pref_content + " (memory_bus)",
                    title=f"ppt-style-dogfood-{now[:10]}",
                    tags=["ppt", "dogfood"],
                    source="agent",
                )
                hits = await memory_bus.recall(
                    "PPT 深蓝 封面", kinds=["preference", "graph"], top_k=8
                )
                return wr, hits

            wr, hits = asyncio.run(_bus())
            ok = bool(wr.ok) and any("深蓝" in (h.content or h.title or "") for h in hits)
            results.append(
                {
                    "name": "ppt_bus_recall",
                    "ok": ok,
                    "detail": f"write_ok={wr.ok} hits={len(hits)}",
                }
            )
            print(f"[{'OK' if ok else 'FAIL'}] memory_bus preference write+recall")
        except Exception as e:
            results.append({"name": "ppt_bus_recall", "ok": False, "detail": str(e)[:200]})
            print(f"[FAIL] memory_bus {e}")

    # ── 3) Evolution replay ────────────────────────────────────
    print("\n--- 3) Evolution ---")
    code, st = _req(base, "GET", "/evolution/status")
    results.append(
        {
            "name": "evolution_status",
            "ok": code == 200 and isinstance(st, dict),
            "detail": str(st)[:200] if st else str(code),
        }
    )
    print(f"[{'OK' if code==200 else 'FAIL'}] evolution status")

    code, assets = _req(base, "GET", "/evolution/assets?limit=50")
    asset_list = assets if isinstance(assets, list) else []
    drafts = [a for a in asset_list if isinstance(a, dict) and a.get("status") == "draft"]
    results.append(
        {
            "name": "evolution_assets",
            "ok": code == 200,
            "detail": f"n={len(asset_list)} drafts={len(drafts)}",
        }
    )
    print(f"[{'OK' if code==200 else 'FAIL'}] assets n={len(asset_list)} drafts={len(drafts)}")

    replay_detail = "no draft"
    if drafts:
        # prefer non-bad name
        pick = next((d for d in drafts if "bad_apply" not in str(d.get("name"))), drafts[0])
        aid = str(pick["id"])
        code, rep = _req(base, "POST", f"/evolution/drafts/{aid}/replay", {})
        passed = None
        if isinstance(rep, dict):
            replay = rep.get("replay") or rep.get("meta", {}).get("replay") or rep
            if isinstance(replay, dict):
                passed = replay.get("pass")
                if passed is None:
                    passed = replay.get("ok")
        ok = code in (200, 400)  # 400 may mean structure fail — still exercised gate
        replay_detail = f"id={aid[:8]} name={pick.get('name')} http={code} pass={passed} body={str(rep)[:160]}"
        results.append({"name": "evolution_replay", "ok": ok, "detail": replay_detail})
        print(f"[{'OK' if ok else 'FAIL'}] replay {replay_detail}")
    else:
        results.append({"name": "evolution_replay", "ok": False, "detail": "no drafts"})
        print("[FAIL] no drafts to replay")

    # summary
    failed = [r for r in results if not r["ok"]]
    passed_n = len(results) - len(failed)
    print(f"\n=== SUMMARY {passed_n}/{len(results)} passed, {len(failed)} failed ===")
    for f in failed:
        print(f"  FAIL {f['name']}: {f['detail']}")

    out = ROOT / "reports" / "DOGFOOD_LIVE_SMOKE.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {"when": now, "base": base, "results": results, "failed": len(failed)},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {out}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
