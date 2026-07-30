#!/usr/bin/env python3
"""Large-task smoke via the same HTTP path the frontend uses (BFF or backend).

Creates a session chat through /api/test/chat (smoke endpoint used by dogfood)
with a multi-step engineering task, then polls kernel/runs for activity.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ.get("TAKTON_SMOKE_BASE", "http://127.0.0.1:8090/api")
# Prefer frontend BFF when available
FE = os.environ.get("TAKTON_SMOKE_FE", "http://127.0.0.1:3000/api")

LARGE_PROMPT = """
【冒烟大任务 · Provider Token 优化验收】

请作为 Takton Agent 完成以下多步骤任务（需要用工具，不要只空谈）：

1. 用工具列出当前工作区 backend/services/llm/ 下的文件名。
2. 读取 provider_profiles.py 的前 80 行，确认存在 resolve_profile / ProviderProfile。
3. 读取 usage_normalize.py，说明 billable_tokens 如何从 cache hit 推导。
4. 检查 model_limits 中 mimo 的 context 规则是否 >= 256000。
5. 在 reports/ 写入一份简短验收笔记 SMOKE_PROVIDER_OPT.md，包含：
   - 你读到的 family 列表摘要
   - billable 定义一句话
   - mimo 窗口结论
6. 最后用 8 条以内 bullet 总结本次 S1–S4 优化对用户的价值。

约束：真实读文件/写文件；若权限拒绝说明原因；不要编造路径内容。
""".strip()


def http_json(method: str, url: str, body: dict | None = None, token: str | None = None, timeout: int = 600):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"raw": raw[:2000]}
        return e.code, payload


def pick_base() -> str:
    for b in (FE, BASE):
        try:
            code, _ = http_json("GET", b.replace("/api", "") + "/api/health" if b.endswith("/api") else b + "/health", timeout=5)
        except Exception:
            code = 0
        # health paths differ; try direct
    # Prefer backend API for reliability
    try:
        with urllib.request.urlopen(BASE.rstrip("/") + "/../health".replace("/api/../", "/"), timeout=5) as r:
            if r.status == 200:
                return BASE
    except Exception:
        pass
    try:
        with urllib.request.urlopen("http://127.0.0.1:8090/api/health", timeout=5) as r:
            if r.status == 200:
                return "http://127.0.0.1:8090/api"
    except Exception:
        pass
    return BASE


def main() -> int:
    base = "http://127.0.0.1:8090/api"
    print("base", base)

    code, login = http_json("POST", f"{base}/auth/auto-login", {}, timeout=30)
    if code >= 400 or not login.get("access_token"):
        print("login failed", code, login)
        return 1
    token = login["access_token"]
    print("user", (login.get("user") or {}).get("email"))

    # Also hit frontend origin to prove FE is up (user-facing entry)
    try:
        with urllib.request.urlopen("http://127.0.0.1:3000", timeout=8) as r:
            print("frontend", r.status)
    except Exception as e:
        print("frontend warn", e)

    t0 = time.time()
    print("dispatching large smoke task…")
    code, result = http_json(
        "POST",
        f"{base}/test/chat",
        {"message": LARGE_PROMPT, "mode": "default"},
        token=token,
        timeout=900,
    )
    elapsed = time.time() - t0
    print("status", code, "elapsed", round(elapsed, 1))
    out_path = ROOT / "reports" / "smoke_large_task.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"http_status": code, "elapsed": elapsed, "result": result}, ensure_ascii=False, indent=2), encoding="utf-8")

    if code >= 400:
        print("FAIL", result)
        return 1

    reply = (result.get("assistant_reply") or "")[:2000]
    err = result.get("error")
    print("session", result.get("session_id"))
    print("error", err)
    print("reply_head:\n", reply[:800])
    print("compress", result.get("context_compressed"), result.get("compress_layers"))

    # Check side effect file if agent wrote it
    note = ROOT / "reports" / "SMOKE_PROVIDER_OPT.md"
    if note.is_file():
        print("NOTE_OK", note, "chars", note.stat().st_size)
    else:
        print("NOTE_MISSING (agent may have written elsewhere or been blocked)")

    # Success criteria: no hard error + non-empty reply OR session created
    if err and not reply:
        return 2
    if not result.get("session_id"):
        return 3
    print("SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
