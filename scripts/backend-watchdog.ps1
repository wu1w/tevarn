# Auto-restart Tevarn backend if port 8090 dies (hard-crash recovery).
# Usage: powershell -ExecutionPolicy Bypass -File scripts\backend-watchdog.ps1
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$py = if (Test-Path "C:\Users\wuyw\AppData\Local\Programs\Python\Python314\python.exe") {
  "C:\Users\wuyw\AppData\Local\Programs\Python\Python314\python.exe"
} else { "python" }
$port = 8090
$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$wlog = Join-Path $logDir "backend-watchdog.log"

function Wlog([string]$m) {
  $line = "$(Get-Date -Format o) $m"
  Add-Content -Path $wlog -Value $line -Encoding utf8
  Write-Host $line
}

$env:PYTHONPATH = $root
if (-not $env:JWT_SECRET) { $env:JWT_SECRET = "tevarn-dev-secret-key-2026" }
if (-not $env:API_KEY) { $env:API_KEY = "tevarn-dev-api-key-2026" }
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:PYTHONUNBUFFERED = "1"
$env:PYTHONFAULTHANDLER = "1"

Wlog "watchdog start root=$root py=$py"

while ($true) {
  $listening = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
  if (-not $listening) {
    Wlog "port $port down — starting backend"
    $blog = Join-Path $logDir "backend-dev.log"
    $berr = Join-Path $logDir "backend-dev.log.err"
    if ((Test-Path $blog) -and ((Get-Item $blog).Length -gt 8MB)) {
      $ts = Get-Date -Format "yyyyMMdd-HHmmss"
      Move-Item $blog "$blog.$ts" -Force -ErrorAction SilentlyContinue
      Move-Item $berr "$berr.$ts" -Force -ErrorAction SilentlyContinue
    }
    $p = Start-Process -FilePath $py `
      -ArgumentList "-u","scripts\run_uvicorn_win.py" `
      -WorkingDirectory $root -WindowStyle Hidden `
      -RedirectStandardOutput $blog -RedirectStandardError $berr -PassThru
    Wlog "started pid=$($p.Id)"
    $ok = $false
    for ($i = 0; $i -lt 40; $i++) {
      Start-Sleep -Seconds 2
      try {
        $r = Invoke-RestMethod "http://127.0.0.1:$port/api/health" -TimeoutSec 2
        if ($r.status -eq "ok") { $ok = $true; break }
      } catch {}
      if ($p.HasExited) {
        Wlog "process exited early code=$($p.ExitCode)"
        break
      }
    }
    if ($ok) { Wlog "health ok pid=$($p.Id)" } else { Wlog "health not ready" }
    Start-Sleep -Seconds 5
  } else {
    Start-Sleep -Seconds 5
  }
}
