# Local backend smoke tests for Tevarn (Windows)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Py = Join-Path $Root "win-python\python.exe"
if (-not (Test-Path $Py)) { $Py = "python" }

function Stop-PortListeners([int]$Port) {
  Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
}

$Port = 8010
Stop-PortListeners 8000
Stop-PortListeners $Port
Start-Sleep -Seconds 1

Write-Host "==> Ensuring backend deps ($Py)"
& $Py -m pip install -r (Join-Path $Root "backend\requirements.txt") --disable-pip-version-check -q

$env:TEVARN_JWT_SECRET = "tevarn-local-dev-jwt-secret-32chars-min"
$env:TEVARN_API_KEY = "tevarn-local-dev-api-key-32chars-min"
$env:TEVARN_SETTINGS_ENCRYPTION_SALT = "tevarn-local-dev-salt-2026"
$env:TEVARN_DB_URL = "sqlite+aiosqlite:///$($Root.Replace('\','/'))/tevarn-dev.db"
$env:TEVARN_APP_HOST = "127.0.0.1"
$env:TEVARN_APP_PORT = "$Port"
$env:TEVARN_SINGLE_USER_MODE = "true"
$env:TEVARN_FILE_BROWSER_ROOT = (Join-Path $Root "workspace")
$env:TEVARN_UPLOADS_DIR = (Join-Path $Root "uploads")
$env:TEVARN_DEFAULT_ADMIN_PASSWORD = "admin"
$env:TEVARN_LOG_LEVEL = "info"

Write-Host "==> Starting backend on :$Port"
$outLog = Join-Path $Root "backend-e2e.log"
$errLog = Join-Path $Root "backend-e2e.err.log"
$backend = Start-Process -FilePath $Py -ArgumentList @(
  "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "$Port"
) -WorkingDirectory $Root -PassThru -WindowStyle Hidden `
  -RedirectStandardOutput $outLog -RedirectStandardError $errLog

try {
  $ok = $false
  $body = $null
  for ($i = 0; $i -lt 50; $i++) {
    if ($backend.HasExited) { throw "Backend exited early; see $errLog" }
    try {
      $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/health" -UseBasicParsing -TimeoutSec 2
      if ($r.StatusCode -eq 200) { $ok = $true; $body = $r.Content; break }
    } catch {
      Start-Sleep -Milliseconds 400
    }
  }
  if (-not $ok) { throw "Backend health check failed; see $errLog" }
  Write-Host "Backend OK: $body"

  Write-Host "==> API smoke tests"
  $login = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$Port/api/auth/auto-login" -ContentType "application/json"
  if (-not $login.access_token) { throw "auto-login failed" }
  Write-Host "auto-login OK user=$($login.user.email)"

  $headers = @{ Authorization = "Bearer $($login.access_token)" }
  $me = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/auth/me" -Headers $headers
  Write-Host "me OK username=$($me.username) superuser=$($me.is_superuser)"

  $files = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/files/info" -Headers $headers
  Write-Host "files sandbox=$($files.sandbox_root)"

  $skills = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/skills" -Headers $headers
  Write-Host "skills count=$($skills.Count)"

  $session = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$Port/api/sessions" -Headers $headers -ContentType "application/json" -Body "{}"
  Write-Host "session created id=$($session.id)"

  Write-Host "==> ALL SMOKE CHECKS PASSED"
}
finally {
  if ($backend -and -not $backend.HasExited) {
    Stop-Process -Id $backend.Id -Force -ErrorAction SilentlyContinue
  }
  Stop-PortListeners $Port
}
