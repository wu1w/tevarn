# -*- coding: utf-8 -*-
"""One-shot workforce status snapshot for owner review."""
from __future__ import annotations

import json
import urllib.request

BASE = "http://127.0.0.1:8090/api"


def get(path: str, timeout: float = 45):
    req = urllib.request.Request(BASE + path)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> None:
    rt = get("/runtime/status")
    print("=== RUNTIME ===")
    print(json.dumps(rt, ensure_ascii=False, indent=2))

    brief = get("/kernel/workspace/brief?hours=24")
    print("\n=== BRIEF HEADLINE ===")
    print(json.dumps(brief.get("headline"), ensure_ascii=False, indent=2))
    print("narrative:", (brief.get("narrative") or {}).get("zh"))
    print("running_employees:", brief.get("running_employees"))
    print("\ncrew:")
    for c in brief.get("crew") or []:
        print(" -", c.get("name"), "|", c.get("role"), "|", c.get("status"))
    print("\nrecent_done:")
    for d in (brief.get("recent_done") or [])[:5]:
        instr = (d.get("instruction") or "").replace("\n", " ")[:160]
        res = (d.get("result") or "").replace("\n", " ")[:240]
        print(" -", d.get("identity_name"), "|", instr)
        print("   result:", res)
    print("recent_failed:", brief.get("recent_failed"))

    ids = get("/kernel/identities")
    print("\n=== IDENTITIES total=%s ===" % ids.get("total"))
    for i in ids.get("identities") or []:
        meta = i.get("meta") or {}
        sid = str(meta.get("workforce_session_id") or "")
        print(
            " -",
            i.get("name"),
            "role=",
            i.get("role"),
            "caps=",
            i.get("capabilities"),
            "source=",
            meta.get("source"),
            "wf_sess=",
            sid[:8] if sid else "-",
        )

    procs = get("/kernel/processes")
    print("\n=== PROCESSES total=%s ===" % procs.get("total"))
    for p in procs.get("processes") or []:
        m = p.get("meta") or {}
        print(
            " - state=",
            p.get("state"),
            "name=",
            m.get("identity_name"),
            "tokens=",
            p.get("tokens_used"),
            "/",
            p.get("token_budget"),
            "inbox=",
            str(m.get("inbox_item_id") or "")[:8],
            "sess=",
            str(p.get("session_id") or "")[:8],
        )

    inbox = get("/kernel/inbox?limit=40")
    print("\n=== INBOX raw keys ===", list(inbox.keys()) if isinstance(inbox, dict) else type(inbox))
    items = []
    if isinstance(inbox, dict):
        items = inbox.get("items") or inbox.get("data") or []
        if not items and "total" in inbox:
            # maybe flat list under another key
            for k, v in inbox.items():
                if isinstance(v, list):
                    items = v
                    break
    elif isinstance(inbox, list):
        items = inbox
    print("inbox count:", len(items))
    for it in items[:20]:
        if not isinstance(it, dict):
            print(" -", it)
            continue
        instr = (it.get("instruction") or it.get("title") or it.get("summary") or "")
        instr = str(instr).replace("\n", " ")[:100]
        print(
            " -",
            it.get("status"),
            "id=",
            str(it.get("id") or "")[:8],
            "ident=",
            it.get("identity_name") or str(it.get("identity_id") or "")[:8],
            "instr=",
            instr,
        )

    try:
        jr = get("/kernel/jobs/running")
        print("\n=== JOBS RUNNING ===")
        print(json.dumps(jr, ensure_ascii=False, indent=2)[:3000])
    except Exception as e:
        print("jobs/running err:", e)


if __name__ == "__main__":
    main()
