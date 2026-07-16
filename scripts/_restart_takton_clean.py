import subprocess
import time
import urllib.request
from pathlib import Path

# kill
subprocess.run(
    ["taskkill", "/F", "/IM", "Takton.exe"],
    capture_output=True,
)
ps_kill = r"""
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -like '*uvicorn backend.main*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
"""
subprocess.run(["powershell.exe", "-Command", ps_kill], capture_output=True)
time.sleep(2)

# ensure code copied
src = Path(r"E:/项目/taktonl-0.1.0/backend")
dst = Path(r"C:/Users/developer/AppData/Local/Programs/Takton/resources/backend")
for rel in [
    "agent/loop.py",
    "services/channel_gateway.py",
    "services/llm/schemas.py",
    "services/llm/openai_compatible.py",
]:
    s, d = src / rel, dst / rel
    d.parent.mkdir(parents=True, exist_ok=True)
    d.write_bytes(s.read_bytes())
    print("copied", rel)

gw = (dst / "services/channel_gateway.py").read_text(encoding="utf-8")
print("progress.ack?", "progress.ack" in gw)
print("hardcoded send ack?", 'await self._send("收到' in gw or "await self._send('收到" in gw)

# start
subprocess.Popen(
    [r"C:\Users\developer\AppData\Local\Programs\Takton\Takton.exe"],
    cwd=r"C:\Users\developer\AppData\Local\Programs\Takton",
)
for i in range(30):
    try:
        print("health", urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=2).read().decode())
        break
    except Exception:
        time.sleep(1)
else:
    print("health FAIL")

out = subprocess.check_output(
    [
        "powershell.exe",
        "-Command",
        "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*uvicorn backend.main*' } | ForEach-Object { $_.ProcessId.ToString() + ' | ' + $_.CommandLine }",
    ],
    text=True,
    errors="replace",
)
print("uvicorn lines:\n", out)
out2 = subprocess.check_output(
    [
        "powershell.exe",
        "-Command",
        "(Get-Process Takton -ErrorAction SilentlyContinue | Measure-Object).Count",
    ],
    text=True,
    errors="replace",
)
print("takton count", out2.strip())
