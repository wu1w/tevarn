import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

src = Path(r"E:/项目/taktonl-0.1.0/backend")
dst = Path(r"C:/Users/developer/AppData/Local/Programs/Takton/resources/backend")

subprocess.run(["taskkill", "/F", "/IM", "Takton.exe"], capture_output=True)
subprocess.run(
    [
        "powershell.exe",
        "-Command",
        "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*uvicorn backend.main*' -or $_.CommandLine -like '*takton_agent*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }",
    ],
    capture_output=True,
)
time.sleep(2)

(dst / "services/remote").mkdir(parents=True, exist_ok=True)
for rel in [
    "api/routes/devices.py",
    "agent/loop.py",
    "services/channel_gateway.py",
]:
    (dst / rel).write_bytes((src / rel).read_bytes())
for f in (src / "services/remote").glob("*.py"):
    (dst / "services/remote" / f.name).write_bytes(f.read_bytes())
print("synced remote routes")

subprocess.Popen(
    [r"C:\Users\developer\AppData\Local\Programs\Takton\Takton.exe"],
    cwd=r"C:\Users\developer\AppData\Local\Programs\Takton",
)

for _ in range(40):
    try:
        print("health", urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=2).read().decode())
        break
    except Exception:
        time.sleep(1)
else:
    print("health fail")
    sys.exit(1)

agent_root = Path(r"C:/Users/developer/AppData/Local/Temp/takton-agent-root")
agent_root.mkdir(parents=True, exist_ok=True)
(agent_root / "demo.txt").write_text("hello-remote\n", encoding="utf-8")
env = os.environ.copy()
env["PYTHONPATH"] = str(Path(r"E:/项目/taktonl-0.1.0/takton-agent"))
agent = subprocess.Popen(
    [
        r"C:\Users\developer\AppData\Local\Programs\Takton\resources\python\python.exe",
        "-m",
        "takton_agent",
        "--host",
        "127.0.0.1",
        "--port",
        "19876",
        "--token",
        "test-token-l1-mvp",
        "--root",
        str(agent_root),
        "--name",
        "win-local",
    ],
    cwd=str(Path(r"E:/项目/taktonl-0.1.0/takton-agent")),
    env=env,
)
time.sleep(1.5)
print("agent pid", agent.pid, "alive", agent.poll() is None)

req = urllib.request.Request(
    "http://127.0.0.1:8000/api/auth/auto-login",
    data=b"{}",
    headers={"Content-Type": "application/json"},
    method="POST",
)
tok = json.loads(urllib.request.urlopen(req, timeout=15).read())["access_token"]
H = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}

try:
    paths = json.loads(
        urllib.request.urlopen("http://127.0.0.1:8000/openapi.json", timeout=10).read()
    )["paths"]
    print("pair path", "/api/devices/pair" in paths)
    print("remote paths", [p for p in paths if "remote" in p or "pair" in p])
except Exception as e:
    print("openapi err", e)

body = json.dumps(
    {"name": "win-local", "host": "127.0.0.1", "port": 19876, "token": "test-token-l1-mvp"}
).encode()
try:
    pair = json.loads(
        urllib.request.urlopen(
            urllib.request.Request(
                "http://127.0.0.1:8000/api/devices/pair",
                data=body,
                headers=H,
                method="POST",
            ),
            timeout=20,
        ).read()
    )
    print(
        "PAIR OK",
        pair.get("id"),
        pair.get("status"),
        (pair.get("config") or {}).get("last_latency_ms"),
    )
    did = pair["id"]
    ex = json.loads(
        urllib.request.urlopen(
            urllib.request.Request(
                f"http://127.0.0.1:8000/api/devices/{did}/remote/exec",
                data=json.dumps({"command": "echo api-exec-ok"}).encode(),
                headers=H,
                method="POST",
            ),
            timeout=30,
        ).read()
    )
    print("EXEC", ex)
    fs = json.loads(
        urllib.request.urlopen(
            urllib.request.Request(
                f"http://127.0.0.1:8000/api/devices/{did}/remote/fs?path=.",
                headers=H,
            ),
            timeout=15,
        ).read()
    )
    print("FS entries", [e["name"] for e in fs.get("entries", [])])

    # @device dispatch unit (async)
    import asyncio

    sys.path.insert(0, r"E:/项目/taktonl-0.1.0")
    # use installed backend path
    sys.path.insert(0, r"C:/Users/developer/AppData/Local/Programs/Takton/resources")

    async def at_test():
        from backend.services.remote.dispatch import try_handle_at_device

        # default admin uid from gateway
        uid = __import__("uuid").UUID("314016d7-a9d5-4719-8371-7ec9301fba0b")
        card = await try_handle_at_device(uid, "@win-local echo at-device-ok")
        print("AT_DEVICE card:\n", card)

    try:
        asyncio.run(at_test())
    except Exception as e:
        print("at_device err", e)

except Exception as e:
    print("FAIL", e)
    if hasattr(e, "read"):
        print(e.read()[:800])
finally:
    agent.terminate()
    try:
        agent.wait(timeout=3)
    except Exception:
        agent.kill()
    print("done")
