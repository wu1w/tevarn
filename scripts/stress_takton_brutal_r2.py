#!/usr/bin/env python
"""第二轮更狠：删会话风暴、重复 resume、畸形 WS 帧、超长 token、连打 auto-login。"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid

import aiohttp

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = "http://127.0.0.1:8090"
API = f"{BASE}/api"


async def main() -> int:
    results = []
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as http:
        # login
        async with http.post(f"{API}/auth/auto-login", json={}) as r:
            tok = (await r.json())["access_token"]
        H = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}

        # 1) create 20 sessions then delete half concurrently
        ids = []
        for i in range(20):
            async with http.post(f"{API}/sessions", headers=H, json={"config": {}}) as r:
                ids.append((await r.json())["id"])
        t0 = time.perf_counter()
        async def del_one(sid):
            async with http.delete(f"{API}/sessions/{sid}", headers=H) as r:
                return r.status
        del_stats = await asyncio.gather(*[del_one(s) for s in ids[:10]])
        async with http.get(f"{API}/health") as r:
            health = r.status == 200
        results.append(("delete_storm", health and all(s in (200, 204) for s in del_stats), f"del={del_stats[:5]}... health={health}"))

        # 2) spam auto-login
        async def al():
            async with http.post(f"{API}/auth/auto-login", json={}) as r:
                return r.status
        al_stats = await asyncio.gather(*[al() for _ in range(50)])
        results.append(("auto_login_spam_x50", all(s == 200 for s in al_stats), f"unique_status={set(al_stats)}"))

        # 3) resume spam on random sessions
        async def res(sid):
            async with http.post(f"{API}/sessions/{sid}/resume", headers=H) as r:
                return r.status
        remain = ids[10:]
        res_stats = await asyncio.gather(*[res(s) for s in remain for _ in range(2)])
        results.append(("resume_spam", all(s < 500 for s in res_stats), f"statuses={Counterish(res_stats)}"))

        # 4) malformed websocket frames
        sid = remain[0] if remain else str(uuid.uuid4())
        ws_url = f"ws://127.0.0.1:8090/api/ws/{sid}?token={tok}"
        bad_ok = True
        try:
            async with aiohttp.ClientSession() as s:
                async with s.ws_connect(ws_url, timeout=10) as ws:
                    await ws.send_str("not-json{{{")
                    await ws.send_str("{}")
                    await ws.send_str(json.dumps({"type": "user_input"}))  # empty content
                    await ws.send_str(json.dumps({"type": "???","x": 1}))
                    await ws.send_bytes(b"\x00\x01\xff")
                    await asyncio.sleep(0.5)
                    await ws.close()
        except Exception as e:
            # still ok if server lives
            bad_ok = True
            detail = str(e)[:60]
        else:
            detail = "closed cleanly"
        async with http.get(f"{API}/health") as r:
            health = r.status == 200
        results.append(("ws_malformed_frames", health, f"{detail} health={health}"))

        # 5) expired/garbage token flood
        async def bad_tok(i):
            h = {"Authorization": f"Bearer garbage.{i}.token", "Content-Type": "application/json"}
            async with http.get(f"{API}/sessions/my", headers=h) as r:
                return r.status
        bt = await asyncio.gather(*[bad_tok(i) for i in range(40)])
        # 单用户模式：无效/缺失 token 会回退默认 admin（设计如此，非崩溃）
        # 通过标准：不出现 5xx，服务仍可健康响应
        results.append((
            "garbage_token_x40_single_user_fallback",
            all(s < 500 for s in bt),
            f"statuses={Counterish(bt)} (single_user allows fallback→200 is expected)",
        ))

        # 6) final health burst
        hb = []
        for _ in range(50):
            async with http.get(f"{API}/health") as r:
                hb.append(r.status)
        results.append(("final_health_burst", all(s == 200 for s in hb), f"ok={hb.count(200)}/50"))

    print("\n===== ROUND 2 =====")
    fail = 0
    for name, ok, detail in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
        if not ok:
            fail += 1
    print(f"PASS={len(results)-fail} FAIL={fail}")
    return 0 if fail == 0 else 1


def Counterish(xs):
    from collections import Counter
    return dict(Counter(xs))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
