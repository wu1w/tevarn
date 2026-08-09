# Durable Tevarn backend launcher (survives parent shell / job exit).
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start-backend-durable.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$venvPy = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
  throw "venv python not found: $venvPy"
}

# free 8090
Get-NetTCPConnection -LocalPort 8090 -State Listen -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object {
    if ($_ -and $_ -gt 0) {
      Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
    }
  }
Start-Sleep -Milliseconds 800

# load .env
if (Test-Path (Join-Path $Root ".env")) {
  Get-Content (Join-Path $Root ".env") | ForEach-Object {
    if ($_ -match '^\s*#' -or $_ -match '^\s*$') { return }
    $parts = $_ -split '=', 2
    if ($parts.Count -eq 2) {
      [Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim(), "Process")
    }
  }
}

$env:PYTHONPATH = $Root
$env:TEVARN_KERNEL_BACKEND = "rust"
$env:TEVARN_KERNEL_AUTO_START = "0"
$env:TEVARN_KERNEL_HOST = "127.0.0.1:17890"
$env:TEVARN_APP_HOST = "127.0.0.1"
$env:TEVARN_APP_PORT = "8090"

$logDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$outLog = Join-Path $logDir "backend-durable.out.log"
$errLog = Join-Path $logDir "backend-durable.err.log"
$pidFile = Join-Path $logDir "backend-durable.pid"

# UseShellExecute=false + redirected streams, but process is started via
# Process.Start with CreateNoWindow — still can die with parent job.
# Prefer cmd start /b with independent process group:
$arg = "-m uvicorn backend.main:app --host 127.0.0.1 --port 8090"
$cmd = "set PYTHONPATH=$Root&& set TEVARN_KERNEL_BACKEND=rust&& set TEVARN_KERNEL_AUTO_START=0&& set TEVARN_KERNEL_HOST=127.0.0.1:17890&& set TEVARN_APP_HOST=127.0.0.1&& set TEVARN_APP_PORT=8090&& `"$venvPy`" $arg >> `"$outLog`" 2>> `"$errLog`""

# start detached via WMI (outside many job objects)
$proc = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
  CommandLine = "`"$venvPy`" $arg"
  CurrentDirectory = $Root
}
if ($proc.ReturnValue -ne 0) {
  throw "Win32_Process.Create failed: $($proc.ReturnValue)"
}
$proc.ProcessId | Set-Content $pidFile -Encoding ascii
Write-Host "backend started pid=$($proc.ProcessId)"

# wait health
for ($i = 0; $i -lt 40; $i++) {
  try {
    $r = Invoke-WebRequest "http://127.0.0.1:8090/api/health" -UseBasicParsing -TimeoutSec 2
    if ($r.StatusCode -eq 200) {
      Write-Host "health ok"
      exit 0
    }
  } catch {
    Start-Sleep -Milliseconds 400
  }
}
Write-Host "health timeout — see $errLog"
exit 1
