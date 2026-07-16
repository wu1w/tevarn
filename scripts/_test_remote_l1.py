import asyncio
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(r"E:/项目/taktonl-0.1.0")
AGENT_PY = Path(r"C:/Users/developer/AppData/Local/Programs/Takton/resources/python/python.exe")
AGENT_ROOT = Path(r"C:/Users/developer/AppData/Local/Temp/takton-agent-root")
AGENT_ROOT.mkdir(parents=True, exist_ok=True)
(AGENT_ROOT / "demo.txt").write_text("hello-remote\n", encoding="utf-8")
TOKEN = "test-token-l1-mvp"
PORT = 19876

# start agent
env = {**dict(**{k: __import__("os").environ[k] for k in __import__("os").environ}), "PYTHONPATH": str(ROOT / "takton-agent")}
proc = subprocess.Popen(
    [
        str(AGENT_PY),
        "-m",
        "takton_agent",
        "--host",
        "127.0.0.1",
        "--port",
        str(PORT),
        "--token",
        TOKEN,
        "--root",
        str(AGENT_ROOT),
        "--name",
        "win-local",
    ],
    cwd=str(ROOT / "takton-agent"),
    env={**__import__("os").environ, "PYTHONPATH": str(ROOT / "takton-agent")},
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
)
time.sleep(1.5)
if proc.poll() is not None:
    print("agent died", proc.stdout.read() if proc.stdout else "")
    sys.exit(1)
print("agent pid", proc.pid)

# direct transport test
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "takton-agent"))


async def main():
    from backend.services.remote.transport import RemoteTransport

    tr = RemoteTransport(f"ws://127.0.0.1:{PORT}", TOKEN, timeout_s=10)
    hello = await tr.call("hello")
    print("hello", hello)
    ping = await tr.ping()
    print("ping", ping)
    listing = await tr.call("file.list", {"path": "."})
    print("list", listing)
    read = await tr.call("file.read", {"path": "demo.txt"})
    print("read", read)
    exe = await tr.call("exec.run", {"command": "echo remote-ok"})
    print("exec", exe)

    # API pair if backend up
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:8000/api/auth/auto-login",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        tok = json.loads(urllib.request.urlopen(req, timeout=10).read())["access_token"]
        body = json.dumps(
            {
                "name": "win-local",
                "host": "127.0.0.1",
                "port": PORT,
                "token": TOKEN,
            }
        ).encode()
        req = urllib.request.Request(
            "http://127.0.0.1:8000/api/devices/pair",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {tok}",
            },
            method="POST",
        )
        try:
            pair = json.loads(urllib.request.urlopen(req, timeout=20).read())
            print("pair", pair.get("id"), pair.get("status"), pair.get("config", {}).get("last_latency_ms"))
            did = pair["id"]
            req = urllib.request.Request(
                f"http://127.0.0.1:8000/api/devices/{did}/remote/exec",
                data=json.dumps({"command": "echo api-exec-ok"}).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {tok}",
                },
                method="POST",
            )
            ex = json.loads(urllib.request.urlopen(req, timeout=30).read())
            print("api exec", ex)
        except Exception as e:
            err = e.read().decode() if hasattr(e, "read") else str(e)
            print("api pair/exec fail", err)
            print("NOTE: backend may not have new routes until restart/sync")
    except Exception as e:
        print("backend login fail", e)


try:
    asyncio.run(main())
finally:
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except Exception:
        proc.kill()
    print("agent stopped")
