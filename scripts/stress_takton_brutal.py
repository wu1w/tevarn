#!/usr/bin/env python
"""Takton 暴力健壮性压测（API + WebSocket）。

用法:
  python scripts/stress_takton_brutal.py [--base http://127.0.0.1:8090]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import sys
import time
import traceback
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import aiohttp

# Windows console
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


@dataclass
class Result:
    name: str
    ok: bool
    detail: str = ""
    ms: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)


class BrutalStress:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.api = f"{self.base}/api"
        self.token: str | None = None
        self.results: list[Result] = []
        self.session_ids: list[str] = []

    def record(self, r: Result) -> None:
        self.results.append(r)
        flag = "PASS" if r.ok else "FAIL"
        print(f"[{flag}] {r.name} ({r.ms:.0f}ms) {r.detail}")

    async def run(self) -> int:
        timeout = aiohttp.ClientTimeout(total=120)
        async with aiohttp.ClientSession(timeout=timeout) as http:
            await self.t_health(http)
            await self.t_auto_login(http)
            if not self.token:
                print("FATAL: no token, abort")
                return 2
            await self.t_flood_health(http)
            await self.t_flood_authenticated(http)
            await self.t_burst_create_sessions(http)
            await self.t_invalid_payloads(http)
            await self.t_huge_json(http)
            await self.t_path_traversalish(http)
            await self.t_ws_flood()
            await self.t_ws_rapid_stop()
            await self.t_ws_huge_message()
            await self.t_ws_concurrent_same_session()
            await self.t_checkpoint_resume_endpoints(http)
            await self.t_server_still_alive(http)

        return self.summary()

    def headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    async def t_health(self, http: aiohttp.ClientSession) -> None:
        t0 = time.perf_counter()
        try:
            async with http.get(f"{self.api}/health") as r:
                body = await r.text()
                ok = r.status == 200 and "takton" in body.lower()
                self.record(Result("health", ok, f"status={r.status} body={body[:80]}", (time.perf_counter()-t0)*1000))
        except Exception as e:
            self.record(Result("health", False, str(e), (time.perf_counter()-t0)*1000))

    async def t_auto_login(self, http: aiohttp.ClientSession) -> None:
        t0 = time.perf_counter()
        try:
            async with http.post(f"{self.api}/auth/auto-login", json={}) as r:
                data = await r.json(content_type=None)
                token = data.get("access_token") or data.get("token")
                ok = r.status == 200 and bool(token)
                if token:
                    self.token = token
                self.record(Result("auto-login", ok, f"status={r.status} keys={list(data)[:6]}", (time.perf_counter()-t0)*1000))
        except Exception as e:
            self.record(Result("auto-login", False, str(e), (time.perf_counter()-t0)*1000))

    async def t_flood_health(self, http: aiohttp.ClientSession) -> None:
        n = 300
        t0 = time.perf_counter()
        statuses: Counter[int] = Counter()
        errs = 0

        async def one():
            nonlocal errs
            try:
                async with http.get(f"{self.api}/health") as r:
                    statuses[r.status] += 1
            except Exception:
                errs += 1

        await asyncio.gather(*[one() for _ in range(n)])
        ms = (time.perf_counter() - t0) * 1000
        ok = statuses.get(200, 0) >= n * 0.95 and errs == 0
        self.record(Result(
            f"flood_health_x{n}",
            ok,
            f"ok={statuses.get(200,0)} 429={statuses.get(429,0)} err={errs} rps={n/(ms/1000):.0f}",
            ms,
            dict(statuses),
        ))

    async def t_flood_authenticated(self, http: aiohttp.ClientSession) -> None:
        n = 200
        t0 = time.perf_counter()
        statuses: Counter[int] = Counter()
        errs = 0

        async def one(i: int):
            nonlocal errs
            try:
                # mix endpoints
                if i % 3 == 0:
                    url = f"{self.api}/sessions/my"
                elif i % 3 == 1:
                    url = f"{self.api}/health"
                else:
                    url = f"{self.api}/settings"
                async with http.get(url, headers=self.headers()) as r:
                    statuses[r.status] += 1
                    await r.read()
            except Exception:
                errs += 1

        await asyncio.gather(*[one(i) for i in range(n)])
        ms = (time.perf_counter() - t0) * 1000
        # under single_user + local, 429 should be rare
        ok = statuses.get(200, 0) + statuses.get(404, 0) >= n * 0.8
        self.record(Result(
            f"flood_auth_mix_x{n}",
            ok,
            f"200={statuses.get(200,0)} 401={statuses.get(401,0)} 429={statuses.get(429,0)} "
            f"5xx={sum(statuses[s] for s in statuses if s>=500)} err={errs}",
            ms,
        ))

    async def t_burst_create_sessions(self, http: aiohttp.ClientSession) -> None:
        n = 40
        t0 = time.perf_counter()
        ok_n = 0
        fail = 0
        ids: list[str] = []

        async def one(i: int):
            nonlocal ok_n, fail
            try:
                async with http.post(
                    f"{self.api}/sessions",
                    headers=self.headers(),
                    json={"config": {"identity": f"stress-{i}", "skills": []}},
                ) as r:
                    if r.status in (200, 201):
                        data = await r.json(content_type=None)
                        sid = str(data.get("id") or "")
                        if sid:
                            ids.append(sid)
                        ok_n += 1
                    else:
                        fail += 1
                        await r.read()
            except Exception:
                fail += 1

        await asyncio.gather(*[one(i) for i in range(n)])
        self.session_ids.extend(ids)
        ms = (time.perf_counter() - t0) * 1000
        self.record(Result(
            f"burst_create_sessions_x{n}",
            ok_n >= n * 0.9,
            f"ok={ok_n} fail={fail} ids={len(ids)}",
            ms,
        ))

    async def t_invalid_payloads(self, http: aiohttp.ClientSession) -> None:
        t0 = time.perf_counter()
        cases = [
            ("POST", f"{self.api}/sessions", {"config": "not-an-object"}),
            ("POST", f"{self.api}/sessions", None),
            ("GET", f"{self.api}/sessions/{uuid.uuid4()}", None),
            ("POST", f"{self.api}/auth/login", {"email": "x", "password": "y"}),
            ("PUT", f"{self.api}/sessions/{uuid.uuid4()}/config", {"config": {"skills": 123}}),
        ]
        survived = 0
        codes = []
        for method, url, body in cases:
            try:
                if method == "GET":
                    async with http.get(url, headers=self.headers()) as r:
                        codes.append(r.status)
                        await r.read()
                elif method == "POST":
                    async with http.post(url, headers=self.headers(), json=body) as r:
                        codes.append(r.status)
                        await r.read()
                else:
                    async with http.put(url, headers=self.headers(), json=body) as r:
                        codes.append(r.status)
                        await r.read()
                survived += 1
            except Exception:
                pass
        # server should not crash
        async with http.get(f"{self.api}/health") as r:
            health_ok = r.status == 200
        self.record(Result(
            "invalid_payloads",
            survived == len(cases) and health_ok,
            f"codes={codes} health={health_ok}",
            (time.perf_counter() - t0) * 1000,
        ))

    async def t_huge_json(self, http: aiohttp.ClientSession) -> None:
        t0 = time.perf_counter()
        # 2MB-ish string in config - should reject or accept without crash
        huge = "暴" * 200_000
        try:
            async with http.post(
                f"{self.api}/sessions",
                headers=self.headers(),
                json={"config": {"identity": huge[:50_000], "sys_prompt": huge[:50_000]}},
            ) as r:
                status = r.status
                await r.read()
            async with http.get(f"{self.api}/health") as r:
                health = r.status == 200
            # any non-5xx is fine as long as alive
            ok = health and status < 500
            self.record(Result("huge_json_session", ok, f"status={status} health={health}", (time.perf_counter()-t0)*1000))
        except Exception as e:
            # timeout/disconnect might happen but check health
            try:
                async with http.get(f"{self.api}/health") as r:
                    health = r.status == 200
            except Exception:
                health = False
            self.record(Result("huge_json_session", health, f"exc={e} health={health}", (time.perf_counter()-t0)*1000))

    async def t_path_traversalish(self, http: aiohttp.ClientSession) -> None:
        t0 = time.perf_counter()
        paths = [
            f"{self.api}/../../../etc/passwd",
            f"{self.base}/uploads/../../windows/win.ini",
            f"{self.api}/sessions/../auth/auto-login",
        ]
        codes = []
        for p in paths:
            try:
                async with http.get(p, headers=self.headers()) as r:
                    codes.append(r.status)
                    await r.read()
            except Exception as e:
                codes.append(str(type(e).__name__))
        async with http.get(f"{self.api}/health") as r:
            health = r.status == 200
        self.record(Result("path_traversalish", health, f"codes={codes}", (time.perf_counter()-t0)*1000))

    async def _ws_url(self) -> str:
        return self.base.replace("http://", "ws://").replace("https://", "wss://") + "/api/ws"

    async def t_ws_flood(self) -> None:
        """并发打开多个 WS，认证后立刻 ping 并关闭。"""
        n = 30
        t0 = time.perf_counter()
        ok_n = 0
        fail = 0
        base_ws = await self._ws_url()

        async def one(i: int):
            nonlocal ok_n, fail
            sid = self.session_ids[i % len(self.session_ids)] if self.session_ids else str(uuid.uuid4())
            url = f"{base_ws}/{sid}?token={self.token}"
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.ws_connect(url, heartbeat=10, timeout=15) as ws:
                        await ws.send_json({"type": "ping"})
                        try:
                            await asyncio.wait_for(ws.receive(), timeout=3)
                        except asyncio.TimeoutError:
                            pass
                        await ws.close()
                        ok_n += 1
            except Exception:
                fail += 1

        await asyncio.gather(*[one(i) for i in range(n)])
        # health after
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"{self.api}/health") as r:
                    health = r.status == 200
        except Exception:
            health = False
        self.record(Result(
            f"ws_flood_x{n}",
            ok_n >= n * 0.6 and health,  # some may fail if session invalid
            f"ok={ok_n} fail={fail} health={health}",
            (time.perf_counter() - t0) * 1000,
        ))

    async def t_ws_rapid_stop(self) -> None:
        """发 user_input 后立即 stop，重复多次。"""
        if not self.session_ids:
            self.record(Result("ws_rapid_stop", False, "no sessions"))
            return
        sid = self.session_ids[0]
        base_ws = await self._ws_url()
        url = f"{base_ws}/{sid}?token={self.token}"
        t0 = time.perf_counter()
        rounds = 15
        ok_rounds = 0
        try:
            async with aiohttp.ClientSession() as s:
                async with s.ws_connect(url, heartbeat=20, timeout=30) as ws:
                    for i in range(rounds):
                        await ws.send_json({
                            "type": "user_input",
                            "content": f"请用一句话介绍你自己 #{i} " + ("测" * 50),
                            "mode": "default",
                        })
                        # immediate stop
                        await asyncio.sleep(0.05)
                        await ws.send_json({"type": "stop"})
                        # drain a bit
                        try:
                            for _ in range(5):
                                msg = await asyncio.wait_for(ws.receive(), timeout=0.5)
                                if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                                    break
                        except asyncio.TimeoutError:
                            pass
                        ok_rounds += 1
                    await ws.close()
        except Exception as e:
            self.record(Result("ws_rapid_stop", False, f"exc={e}", (time.perf_counter()-t0)*1000))
            return

        # still healthy?
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"{self.api}/health") as r:
                    health = r.status == 200
        except Exception:
            health = False
        self.record(Result(
            "ws_rapid_stop",
            ok_rounds >= rounds * 0.8 and health,
            f"rounds={ok_rounds}/{rounds} health={health}",
            (time.perf_counter() - t0) * 1000,
        ))

    async def t_ws_huge_message(self) -> None:
        if not self.session_ids:
            self.record(Result("ws_huge_message", False, "no sessions"))
            return
        sid = self.session_ids[min(1, len(self.session_ids)-1)]
        base_ws = await self._ws_url()
        url = f"{base_ws}/{sid}?token={self.token}"
        t0 = time.perf_counter()
        # ~120k chars - should soft/hard truncate not crash
        payload = "大" * 120_000
        try:
            async with aiohttp.ClientSession() as s:
                async with s.ws_connect(url, heartbeat=30, timeout=60) as ws:
                    await ws.send_json({
                        "type": "user_input",
                        "content": payload,
                        "mode": "default",
                    })
                    # wait a bit then stop to not hang forever if LLM hangs
                    await asyncio.sleep(2)
                    await ws.send_json({"type": "stop"})
                    try:
                        for _ in range(10):
                            msg = await asyncio.wait_for(ws.receive(), timeout=1)
                            if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                                break
                    except asyncio.TimeoutError:
                        pass
                    await ws.close()
            async with aiohttp.ClientSession() as s:
                async with s.get(f"{self.api}/health") as r:
                    health = r.status == 200
            self.record(Result("ws_huge_message", health, f"health={health}", (time.perf_counter()-t0)*1000))
        except Exception as e:
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(f"{self.api}/health") as r:
                        health = r.status == 200
            except Exception:
                health = False
            self.record(Result("ws_huge_message", health, f"exc={e} health={health}", (time.perf_counter()-t0)*1000))

    async def t_ws_concurrent_same_session(self) -> None:
        """同一 session 多连接互相踢 / 并发输入。"""
        if not self.session_ids:
            self.record(Result("ws_concurrent_same_session", False, "no sessions"))
            return
        sid = self.session_ids[0]
        base_ws = await self._ws_url()
        url = f"{base_ws}/{sid}"
        t0 = time.perf_counter()
        results = []

        async def client(tag: str):
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.ws_connect(url + f"?token={self.token}", heartbeat=15, timeout=20) as ws:
                        await asyncio.sleep(0.2)
                        await ws.send_json({
                            "type": "user_input",
                            "content": f"concurrent {tag}",
                            "mode": "default",
                        })
                        await asyncio.sleep(0.3)
                        await ws.send_json({"type": "stop"})
                        await asyncio.sleep(0.2)
                        await ws.close()
                        results.append((tag, True, ""))
            except Exception as e:
                results.append((tag, False, str(e)[:80]))

        await asyncio.gather(*[client(f"c{i}") for i in range(8)])
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"{self.api}/health") as r:
                    health = r.status == 200
        except Exception:
            health = False
        ok_n = sum(1 for _, ok, _ in results if ok)
        self.record(Result(
            "ws_concurrent_same_session",
            health and ok_n >= 1,  # at least some survive; kick is ok
            f"ok_clients={ok_n}/8 health={health} sample={results[:3]}",
            (time.perf_counter() - t0) * 1000,
        ))

    async def t_checkpoint_resume_endpoints(self, http: aiohttp.ClientSession) -> None:
        if not self.session_ids:
            self.record(Result("checkpoint_resume_api", False, "no sessions"))
            return
        sid = self.session_ids[0]
        t0 = time.perf_counter()
        try:
            async with http.get(f"{self.api}/sessions/{sid}/checkpoint", headers=self.headers()) as r:
                st1 = r.status
                body1 = await r.text()
            # resume with nothing should not 500
            async with http.post(f"{self.api}/sessions/{sid}/resume", headers=self.headers()) as r:
                st2 = r.status
                body2 = await r.text()
            async with http.get(f"{self.api}/health") as r:
                health = r.status == 200
            ok = st1 < 500 and st2 < 500 and health
            self.record(Result(
                "checkpoint_resume_api",
                ok,
                f"checkpoint={st1} resume={st2} health={health} body1={body1[:60]}",
                (time.perf_counter() - t0) * 1000,
            ))
        except Exception as e:
            self.record(Result("checkpoint_resume_api", False, str(e), (time.perf_counter()-t0)*1000))

    async def t_server_still_alive(self, http: aiohttp.ClientSession) -> None:
        t0 = time.perf_counter()
        latencies = []
        ok = True
        for _ in range(20):
            try:
                a = time.perf_counter()
                async with http.get(f"{self.api}/health") as r:
                    if r.status != 200:
                        ok = False
                    await r.read()
                latencies.append((time.perf_counter() - a) * 1000)
            except Exception:
                ok = False
                latencies.append(9999)
        p50 = statistics.median(latencies) if latencies else 0
        p95 = sorted(latencies)[int(len(latencies)*0.95)-1] if latencies else 0
        self.record(Result(
            "post_stress_health",
            ok and p95 < 2000,
            f"p50={p50:.0f}ms p95={p95:.0f}ms",
            (time.perf_counter() - t0) * 1000,
        ))

    def summary(self) -> int:
        passed = sum(1 for r in self.results if r.ok)
        failed = sum(1 for r in self.results if not r.ok)
        print("\n========== BRUTAL STRESS SUMMARY ==========")
        print(f"PASS={passed} FAIL={failed} TOTAL={len(self.results)}")
        if failed:
            print("--- failures ---")
            for r in self.results:
                if not r.ok:
                    print(f"  - {r.name}: {r.detail}")
        print("===========================================\n")
        return 0 if failed == 0 else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8090")
    args = ap.parse_args()
    stress = BrutalStress(args.base)
    code = asyncio.run(stress.run())
    raise SystemExit(code)


if __name__ == "__main__":
    main()
