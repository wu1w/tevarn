# Takton one-line installer for Windows (PowerShell)
# Usage:
#   irm https://raw.githubusercontent.com/wu1w/takton/main/scripts/install.ps1 | iex
#   # local:
#   powershell -ExecutionPolicy Bypass -File scripts/install.ps1
#
# Env (optional):
#   $env:TAKTON_HOME = "$env:USERPROFILE\.takton"
#   $env:TAKTON_PORT = "8090"
#   $env:TAKTON_NO_START = "1"
#   $env:TAKTON_SOURCE = "D:\path\to\takton"   # skip git clone

$ErrorActionPreference = "Stop"

$TAKTON_HOME = if ($env:TAKTON_HOME) { $env:TAKTON_HOME } else { Join-Path $env:USERPROFILE ".takton" }
$TAKTON_REPO = if ($env:TAKTON_REPO) { $env:TAKTON_REPO } else { "https://github.com/wu1w/takton.git" }
$TAKTON_REF  = if ($env:TAKTON_REF)  { $env:TAKTON_REF }  else { "main" }
$TAKTON_PORT = if ($env:TAKTON_PORT) { $env:TAKTON_PORT } else { "8090" }
$TAKTON_NO_START = if ($env:TAKTON_NO_START) { $env:TAKTON_NO_START } else { "0" }
$VENV = Join-Path $TAKTON_HOME "venv"
$SRC  = Join-Path $TAKTON_HOME "src"

function Info([string]$m) { Write-Host "[takton] $m" }
function Die([string]$m)  { Write-Host "[takton] ERROR: $m" -ForegroundColor Red; exit 1 }

Write-Host "Takton installer (Windows)" -ForegroundColor Cyan

# Python
$py = $null
foreach ($c in @("py", "python", "python3")) {
  try {
    $v = & $c -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')" 2>$null
    if ($LASTEXITCODE -eq 0 -and $v) {
      $parts = $v.Trim().Split(".")
      if ([int]$parts[0] -gt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 10)) {
        $py = $c
        break
      }
    }
  } catch {}
}
if (-not $py) { Die "需要 Python >= 3.10。请从 https://www.python.org/downloads/ 安装并勾选 Add to PATH。" }
Info "Using Python launcher: $py ($(& $py --version 2>&1))"

# git
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  Die "需要 git。请安装 Git for Windows: https://git-scm.com/download/win"
}

New-Item -ItemType Directory -Force -Path $TAKTON_HOME | Out-Null

if ($env:TAKTON_SOURCE) {
  $SRC = (Resolve-Path $env:TAKTON_SOURCE).Path
  Info "Using existing source: $SRC"
} elseif ((Test-Path ".\backend\main.py") -and (Test-Path ".\pyproject.toml")) {
  $SRC = (Resolve-Path ".").Path
  Info "Using current directory: $SRC"
} else {
  if (Test-Path (Join-Path $SRC ".git")) {
    Info "Updating $SRC ($TAKTON_REF) ..."
    git -C $SRC fetch --depth 1 origin $TAKTON_REF
    git -C $SRC checkout -q FETCH_HEAD
  } else {
    Info "Cloning $TAKTON_REPO ($TAKTON_REF) -> $SRC"
    if (Test-Path $SRC) { Remove-Item -Recurse -Force $SRC }
    git clone --depth 1 --branch $TAKTON_REF $TAKTON_REPO $SRC
    if ($LASTEXITCODE -ne 0) {
      git clone --depth 1 $TAKTON_REPO $SRC
    }
  }
}

if (-not (Test-Path (Join-Path $SRC "backend\main.py"))) {
  Die "源码不完整: backend\main.py 不存在"
}

Info "Creating venv at $VENV"
& $py -m venv $VENV
$pip = Join-Path $VENV "Scripts\pip.exe"
$python = Join-Path $VENV "Scripts\python.exe"
& $python -m pip install -U pip setuptools wheel -q

Info "Installing Takton ..."
$prodReq = Join-Path $SRC "backend\requirements-prod.txt"
if (Test-Path $prodReq) {
  & $pip install -r $prodReq -q
}
& $pip install -e $SRC -q

$envFile = Join-Path $TAKTON_HOME ".env"
if (-not (Test-Path $envFile)) {
  Info "Writing $envFile"
  @"
# Takton local config
TAKTON_PORT=$TAKTON_PORT
"@ | Set-Content -Path $envFile -Encoding UTF8
}

# shim
$binDir = Join-Path $TAKTON_HOME "bin"
New-Item -ItemType Directory -Force -Path $binDir | Out-Null
$shim = Join-Path $binDir "takton.cmd"
@"
@echo off
"$python" -m backend.cli %*
"@ | Set-Content -Path $shim -Encoding ASCII

# PATH hint
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$binDir*") {
  Info "Add to User PATH: $binDir (optional)"
  try {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$binDir", "User")
    $env:Path = "$env:Path;$binDir"
    Info "PATH updated for current user"
  } catch {
    Info "Could not update PATH automatically. Run via: $shim"
  }
}

Info "Install complete."
Info "  Source: $SRC"
Info "  Start:  $shim start --port $TAKTON_PORT"
Info "  Or:     & '$python' -m backend.cli start --port $TAKTON_PORT"

if ($TAKTON_NO_START -ne "1") {
  Info "Starting on http://127.0.0.1:$TAKTON_PORT ..."
  & $python -m backend.cli start --port $TAKTON_PORT
}
