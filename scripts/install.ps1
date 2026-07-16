# Takton one-line installer for Windows (PowerShell)
# Usage:
#   irm https://raw.githubusercontent.com/wu1w/takton/main/scripts/install.ps1 | iex
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

function Get-PythonCmd {
  # Prefer 3.11/3.12/3.13 — 3.14 still breaks many wheels (pydantic-core etc.)
  $candidates = @()
  if (Get-Command py -ErrorAction SilentlyContinue) {
    foreach ($v in @("3.11", "3.12", "3.13", "3.10")) {
      try {
        $out = & py "-$v" -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) { $candidates += $out.Trim() }
      } catch {}
    }
  }
  foreach ($c in @("python3.11", "python3.12", "python3.13", "python3.10", "python3", "python")) {
    if (Get-Command $c -ErrorAction SilentlyContinue) {
      try {
        $exe = & $c -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $exe) { $candidates += $exe.Trim() }
      } catch {}
    }
  }
  # uv-managed CPython common path
  $uvRoot = Join-Path $env:APPDATA "uv\python"
  if (Test-Path $uvRoot) {
    Get-ChildItem $uvRoot -Directory -ErrorAction SilentlyContinue |
      Where-Object { $_.Name -match "cpython-3\.(11|12|13)" } |
      ForEach-Object {
        $p = Join-Path $_.FullName "python.exe"
        if (Test-Path $p) { $candidates += $p }
      }
  }

  foreach ($exe in $candidates) {
    try {
      $ver = & $exe -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')" 2>$null
      if (-not $ver) { continue }
      $parts = $ver.Trim().Split(".")
      $maj = [int]$parts[0]; $min = [int]$parts[1]
      if ($maj -eq 3 -and $min -ge 10 -and $min -le 13) {
        return @{ Exe = $exe; Ver = $ver.Trim() }
      }
    } catch {}
  }
  return $null
}

$pyInfo = Get-PythonCmd
if (-not $pyInfo) {
  Die "需要 Python 3.10–3.13（推荐 3.11/3.12）。当前若只有 3.14，请先安装 3.11：https://www.python.org/downloads/"
}
$pyExe = $pyInfo.Exe
Info "Using Python: $pyExe ($($pyInfo.Ver))"

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
    $cloneOk = $true
    git clone --depth 1 --branch $TAKTON_REF $TAKTON_REPO $SRC 2>$null
    if ($LASTEXITCODE -ne 0) {
      git clone --depth 1 $TAKTON_REPO $SRC
      if ($LASTEXITCODE -ne 0) { $cloneOk = $false }
    }
    if (-not $cloneOk) { Die "git clone 失败" }
  }
}

if (-not (Test-Path (Join-Path $SRC "backend\main.py"))) {
  Die "源码不完整: backend\main.py 不存在"
}

Info "Creating venv at $VENV"
if (Test-Path $VENV) { Remove-Item -Recurse -Force $VENV }
& $pyExe -m venv $VENV --clear
$python = Join-Path $VENV "Scripts\python.exe"
if (-not (Test-Path $python)) { Die "venv 创建失败: $python" }

# Isolate from ambient PYTHONPATH / other venvs
$env:VIRTUAL_ENV = $VENV
$env:PYTHONPATH = ""
$env:PYTHONNOUSERSITE = "1"
$env:PATH = "$(Join-Path $VENV 'Scripts');$env:PATH"

& $python -m pip install -U pip setuptools wheel -q

Info "Installing Takton into isolated venv ..."
$prodReq = Join-Path $SRC "backend\requirements-prod.txt"
if (Test-Path $prodReq) {
  & $python -m pip install -r $prodReq -q
}
& $python -m pip install -e $SRC -q

# sanity import
& $python -c "import fastapi, uvicorn, backend.main; print('import_ok')"
if ($LASTEXITCODE -ne 0) { Die "依赖导入失败，请检查 Python 版本与网络" }

$envFile = Join-Path $TAKTON_HOME ".env"
if (-not (Test-Path $envFile)) {
  Info "Writing $envFile"
  @"
# Takton local config
TAKTON_PORT=$TAKTON_PORT
"@ | Set-Content -Path $envFile -Encoding UTF8
}

$binDir = Join-Path $TAKTON_HOME "bin"
New-Item -ItemType Directory -Force -Path $binDir | Out-Null
$shim = Join-Path $binDir "takton.cmd"
@"
@echo off
set VIRTUAL_ENV=$VENV
set PYTHONPATH=
set PYTHONNOUSERSITE=1
"$python" -m backend.cli %*
"@ | Set-Content -Path $shim -Encoding ASCII

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$binDir*") {
  try {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$binDir", "User")
    $env:Path = "$env:Path;$binDir"
    Info "PATH updated for current user (+ $binDir)"
  } catch {
    Info "Could not update PATH automatically. Use: $shim"
  }
}

Info "Install complete."
Info "  Source: $SRC"
Info "  Start:  $shim start --port $TAKTON_PORT"

if ($TAKTON_NO_START -ne "1") {
  Info "Starting on http://127.0.0.1:$TAKTON_PORT ..."
  & $python -m backend.cli start --port $TAKTON_PORT
}
