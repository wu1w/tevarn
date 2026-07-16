"""Kill old Takton, sync local backend into installed app, start Takton.exe, wait health."""
from __future__ import annotations

import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

SRC = Path(__file__).parent.parent / "backend"
DST = Path.home() / "AppData" / "Local" / "Programs" / "Takton" / "resources" / "backend"
EXE = Path.home() / "AppData" / "Local" / "Programs" / "Takton" / "Takton.exe"
HEALTHS = (
    "http://127.0.0.1:8000/api/health",
    "http://127.0.0.1:8090/api/health",
)

SKIP_DIRS = {"__pycache__", ".pytest_cache", "node_modules", ".git", "data", "logs", "uploads"}


def kill() -> None:
    subprocess.run(["taskkill", "/F", "/IM", "Takton.exe"], capture_output=True)
    # kill uvicorn/backend.cli related python
    ps = r"""
$patterns = @('*uvicorn backend.main*', '*backend.cli*', '*taktonl-0.1.0*backend*', '*Programs\\Takton*uvicorn*')
Get-CimInstance Win32_Process | Where-Object {
  $cl = $_.CommandLine
  if (-not $cl) { return $false }
  foreach ($p in $patterns) { if ($cl -like $p) { return $true } }
  return $false
} | ForEach-Object {
  Write-Host ("kill {0} {1}" -f $_.ProcessId, $_.CommandLine.Substring(0, [Math]::Min(120, $_.CommandLine.Length)))
  Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}
# free common ports
foreach ($port in 8000,8001,8090) {
  $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
  foreach ($c in $conns) {
    try { Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue; Write-Host "kill port $port pid $($c.OwningProcess)" } catch {}
  }
}
"""
    subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps], capture_output=False)
    time.sleep(2)


def sync_backend() -> None:
    if not SRC.is_dir() or not DST.is_dir():
        raise SystemExit(f"missing src/dst: {SRC} {DST}")
    copied = 0
    for path in SRC.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        # skip huge static rebuild noise optional - still sync static for UI
        rel = path.relative_to(SRC)
        dest = DST / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            if dest.exists() and dest.stat().st_size == path.stat().st_size and dest.stat().st_mtime >= path.stat().st_mtime:
                continue
        except OSError:
            pass
        shutil.copy2(path, dest)
        copied += 1
    print(f"synced files: {copied}")
    # quick markers
    for rel in [
        "evolution/manager.py",
        "services/sft_collector.py",
        "api/routes/evolution.py",
        "agent/loop.py",
    ]:
        ok = (DST / rel).exists()
        print(f"  {rel}: {'OK' if ok else 'MISSING'}")


def start() -> None:
    if not EXE.exists():
        raise SystemExit(f"missing {EXE}")
    subprocess.Popen([str(EXE)], cwd=str(EXE.parent))
    print("started Takton.exe")


def wait_health() -> str | None:
    for i in range(40):
        for url in HEALTHS:
            try:
                body = urllib.request.urlopen(url, timeout=2).read().decode()
                print(f"health {url} -> {body}")
                return url
            except Exception:
                pass
        time.sleep(1)
    print("health FAIL")
    return None


def main() -> None:
    print("=== kill ===")
    kill()
    print("=== sync ===")
    sync_backend()
    print("=== start ===")
    start()
    print("=== wait ===")
    url = wait_health()
    # process counts
    out = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            "(Get-Process Takton -ErrorAction SilentlyContinue | Measure-Object).Count",
        ],
        capture_output=True,
        text=True,
    )
    print("takton processes:", (out.stdout or "").strip())
    if not url:
        raise SystemExit(1)
    print("READY", url)


if __name__ == "__main__":
    main()
