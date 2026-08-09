# Tevarn 后端一键重启（Windows，独立进程脱离调用方生命周期）
# 用法: powershell -ExecutionPolicy Bypass -File scripts\dev-backend.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$py = "C:\Users\developer\AppData\Local\Programs\Python\Python314\python.exe"
$port = 8090

# 1. 清端口占用（含 zombie）
$conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($conns) {
  $conns | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object {
    try { Stop-Process -Id $_ -Force -ErrorAction Stop; Write-Host "killed PID $_" } catch {}
  }
  Start-Sleep -Seconds 2
}

# 2. 拉起独立隐藏进程（不依赖当前控制台）
$env:PYTHONPATH = $root
$env:JWT_SECRET = "tevarn-dev-secret-key-2026"
$env:API_KEY = "tevarn-dev-api-key-2026"
$log = Join-Path $root "logs\backend-dev.log"
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
$args = "-m uvicorn backend.main:app --host 127.0.0.1 --port $port"
$p = Start-Process -FilePath $py -ArgumentList $args -WorkingDirectory $root `
  -WindowStyle Hidden -RedirectStandardOutput $log -RedirectStandardError "$log.err" -PassThru
Write-Host "backend started PID $($p.Id)"

# 3. 健康检查
$ok = $false
foreach ($i in 1..30) {
  Start-Sleep -Seconds 2
  try {
    $r = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/health" -TimeoutSec 2
    if ($r.status -eq 'ok') { $ok = $true; break }
  } catch {}
}
if ($ok) { Write-Host "health OK" } else { Write-Host "health TIMEOUT - see $log"; exit 1 }
