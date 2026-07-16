# Takton one-line installer for Windows (PowerShell)
# 小白整行粘贴：
#   irm https://raw.githubusercontent.com/wu1w/takton/main/scripts/install.ps1 | iex
#
# 没有 Python / 只有 3.14 也行：脚本会自动用 uv 下载便携 Python 3.12。
# 可选：
#   $env:TAKTON_HOME / TAKTON_PORT / TAKTON_NO_START / TAKTON_SOURCE / TAKTON_REPO / TAKTON_REF

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$TAKTON_HOME = if ($env:TAKTON_HOME) { $env:TAKTON_HOME } else { Join-Path $env:USERPROFILE ".takton" }
$TAKTON_REPO = if ($env:TAKTON_REPO) { $env:TAKTON_REPO } else { "https://github.com/wu1w/takton.git" }
$TAKTON_REF  = if ($env:TAKTON_REF)  { $env:TAKTON_REF }  else { "main" }
$TAKTON_PORT = if ($env:TAKTON_PORT) { $env:TAKTON_PORT } else { "8090" }
$TAKTON_NO_START = if ($env:TAKTON_NO_START) { $env:TAKTON_NO_START } else { "0" }
$VENV = Join-Path $TAKTON_HOME "venv"
$SRC  = Join-Path $TAKTON_HOME "src"
$TOOLS = Join-Path $TAKTON_HOME "tools"

function Info([string]$m) { Write-Host "[takton] $m" -ForegroundColor Gray }
function Ok([string]$m)   { Write-Host "[takton] ✓ $m" -ForegroundColor Green }
function Die([string]$m)  { Write-Host "[takton] ERROR: $m" -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "  Takton 一键安装 (Windows) — 尽量零脑" -ForegroundColor Cyan
Write-Host "  自动：准备 Python → 下载代码 → 建环境 → 装依赖 → 自检 → 启动" -ForegroundColor DarkGray
Write-Host ""

function Get-SystemPython {
  $candidates = New-Object System.Collections.Generic.List[string]
  if (Get-Command py -ErrorAction SilentlyContinue) {
    foreach ($v in @("3.12", "3.11", "3.13", "3.10")) {
      try {
        $out = & py "-$v" -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) { [void]$candidates.Add($out.Trim()) }
      } catch {}
    }
  }
  foreach ($c in @("python3.12", "python3.11", "python3.13", "python3.10", "python")) {
    if (Get-Command $c -ErrorAction SilentlyContinue) {
      try {
        $exe = & $c -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $exe) { [void]$candidates.Add($exe.Trim()) }
      } catch {}
    }
  }
  $uvRoot = Join-Path $env:APPDATA "uv\python"
  if (Test-Path $uvRoot) {
    Get-ChildItem $uvRoot -Directory -ErrorAction SilentlyContinue |
      Where-Object { $_.Name -match "cpython-3\.(1[0-3])" } |
      ForEach-Object {
        $p = Join-Path $_.FullName "python.exe"
        if (Test-Path $p) { [void]$candidates.Add($p) }
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

function Ensure-Uv {
  if (Get-Command uv -ErrorAction SilentlyContinue) {
    return (Get-Command uv).Source
  }
  $localUv = Join-Path $TOOLS "uv.exe"
  if (Test-Path $localUv) { return $localUv }

  New-Item -ItemType Directory -Force -Path $TOOLS | Out-Null
  Info "未找到合适系统 Python，正在安装 uv（用于自动下载便携 Python）..."
  $zip = Join-Path $env:TEMP "uv-win.zip"
  $url = "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip"
  try {
    Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
  } catch {
    Die "下载 uv 失败。请检查网络，或手动安装 Python 3.12：https://www.python.org/downloads/"
  }
  Expand-Archive -Path $zip -DestinationPath $TOOLS -Force
  $found = Get-ChildItem $TOOLS -Recurse -Filter "uv.exe" | Select-Object -First 1
  if (-not $found) { Die "uv 解压失败" }
  Copy-Item $found.FullName $localUv -Force
  Ok "uv 已就绪: $localUv"
  return $localUv
}

function Ensure-Python {
  $sys = Get-SystemPython
  if ($sys) {
    Ok "使用系统 Python $($sys.Ver)"
    return $sys.Exe
  }
  $uv = Ensure-Uv
  Info "用 uv 安装便携 Python 3.12（只需一次，装在用户目录）..."
  & $uv python install 3.12
  if ($LASTEXITCODE -ne 0) { Die "uv python install 3.12 失败" }
  $pyOut = & $uv python find 3.12 2>$null
  if ($LASTEXITCODE -ne 0 -or -not $pyOut) {
    Die "找不到 uv 安装的 Python 3.12，请重开终端后重试"
  }
  $py = $pyOut.ToString().Trim()
  if (-not (Test-Path $py)) {
    Die "Python 路径无效: $py"
  }
  Ok "便携 Python: $py"
  return $py
}

# --- preflight ---
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  Die "需要 Git。请安装后重开 PowerShell：https://git-scm.com/download/win"
}
Ok "Git 已就绪"

$pyExe = Ensure-Python

New-Item -ItemType Directory -Force -Path $TAKTON_HOME | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $TAKTON_HOME "data\uploads") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $TAKTON_HOME "data\workspace") | Out-Null

# --- source ---
if ($env:TAKTON_SOURCE) {
  $SRC = (Resolve-Path $env:TAKTON_SOURCE).Path
  Info "使用本地源码: $SRC"
} elseif ((Test-Path ".\backend\main.py") -and (Test-Path ".\pyproject.toml")) {
  $SRC = (Resolve-Path ".").Path
  Info "使用当前目录: $SRC"
} else {
  if (Test-Path (Join-Path $SRC ".git")) {
    Info "更新源码..."
    git -C $SRC fetch --depth 1 origin $TAKTON_REF
    if ($LASTEXITCODE -ne 0) { Die "git fetch 失败" }
    git -C $SRC checkout -q FETCH_HEAD
  } else {
    Info "从 GitHub 下载（首次 1–3 分钟）..."
    if (Test-Path $SRC) { Remove-Item -Recurse -Force $SRC }
    git clone --depth 1 --branch $TAKTON_REF $TAKTON_REPO $SRC 2>$null
    if ($LASTEXITCODE -ne 0) { git clone --depth 1 $TAKTON_REPO $SRC }
    if ($LASTEXITCODE -ne 0) { Die "git clone 失败，请确认能访问 github.com" }
  }
  Ok "源码就绪"
}

if (-not (Test-Path (Join-Path $SRC "backend\main.py"))) {
  Die "源码不完整: 缺少 backend\main.py"
}

# --- venv + deps (isolated "portable env") ---
Info "创建独立运行环境（类似打包好的 venv，但可在本机正确生成）..."
if (Test-Path $VENV) { Remove-Item -Recurse -Force $VENV }

$uvCmd = $null
if (Get-Command uv -ErrorAction SilentlyContinue) { $uvCmd = (Get-Command uv).Source }
elseif (Test-Path (Join-Path $TOOLS "uv.exe")) { $uvCmd = Join-Path $TOOLS "uv.exe" }

if ($uvCmd) {
  & $uvCmd venv $VENV --python $pyExe --clear
} else {
  & $pyExe -m venv $VENV --clear
}
$python = Join-Path $VENV "Scripts\python.exe"
if (-not (Test-Path $python)) { Die "环境创建失败: $python" }

$env:VIRTUAL_ENV = $VENV
$env:PYTHONPATH = ""
$env:PYTHONHOME = ""
$env:PYTHONNOUSERSITE = "1"
$env:PATH = "$(Join-Path $VENV 'Scripts');$env:PATH"

Info "安装依赖（自动下载，请保持网络畅通）..."
if ($uvCmd) {
  $prodReq = Join-Path $SRC "backend\requirements-prod.txt"
  if (Test-Path $prodReq) {
    & $uvCmd pip install -r $prodReq --python $python
  } else {
    & $uvCmd pip install -r (Join-Path $SRC "backend\requirements.txt") --python $python
  }
  if ($LASTEXITCODE -ne 0) { Die "依赖安装失败" }
  & $uvCmd pip install -e $SRC --python $python
} else {
  & $python -m pip install -U pip setuptools wheel -q
  $prodReq = Join-Path $SRC "backend\requirements-prod.txt"
  if (Test-Path $prodReq) {
    & $python -m pip install -r $prodReq -q
  } else {
    & $python -m pip install -r (Join-Path $SRC "backend\requirements.txt") -q
  }
  if ($LASTEXITCODE -ne 0) { Die "依赖安装失败" }
  & $python -m pip install -e $SRC -q
}
if ($LASTEXITCODE -ne 0) { Die "Takton 安装失败" }

Info "自检..."
& $python -c "import fastapi, uvicorn, sqlalchemy, aiosqlite, httpx, jose, backend.main; print('import_ok')"
if ($LASTEXITCODE -ne 0) { Die "自检失败" }
Ok "运行环境就绪（$VENV）"

# --- env secrets ---
$envFile = Join-Path $TAKTON_HOME ".env"
if (-not (Test-Path $envFile)) {
  Info "生成本地密钥与配置..."
  $jwt = & $python -c "import secrets; print(secrets.token_hex(32))"
  $api = & $python -c "import secrets; print(secrets.token_hex(32))"
  $salt = & $python -c "import secrets; print(secrets.token_hex(16))"
  $dbPath = ((Join-Path $TAKTON_HOME "data\takton.db") -replace '\\', '/')
  $up = ((Join-Path $TAKTON_HOME "data\uploads") -replace '\\', '/')
  $ws = ((Join-Path $TAKTON_HOME "data\workspace") -replace '\\', '/')
  @"
# Auto-generated — do not commit
TAKTON_JWT_SECRET=$jwt
TAKTON_API_KEY=$api
TAKTON_SETTINGS_ENCRYPTION_SALT=$salt
TAKTON_DB_URL=sqlite+aiosqlite:///$dbPath
TAKTON_APP_HOST=127.0.0.1
TAKTON_APP_PORT=$TAKTON_PORT
TAKTON_SINGLE_USER_MODE=true
TAKTON_UPLOADS_DIR=$up
TAKTON_FILE_BROWSER_ROOT=$ws
TAKTON_LOG_LEVEL=info
"@ | Set-Content -Path $envFile -Encoding UTF8
  Ok "配置: $envFile"
}

$binDir = Join-Path $TAKTON_HOME "bin"
New-Item -ItemType Directory -Force -Path $binDir | Out-Null
$shim = Join-Path $binDir "takton.cmd"
@"
@echo off
setlocal
set VIRTUAL_ENV=$VENV
set PYTHONPATH=
set PYTHONHOME=
set PYTHONNOUSERSITE=1
set PATH=$VENV\Scripts;%PATH%
if exist "$envFile" (
  for /f "usebackq eol=# tokens=1,* delims==" %%A in ("$envFile") do (
    if not "%%A"=="" set "%%A=%%B"
  )
)
"$python" -m backend.cli %*
"@ | Set-Content -Path $shim -Encoding ASCII

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$binDir*") {
  try {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$binDir", "User")
    $env:Path = "$env:Path;$binDir"
  } catch {}
}

Write-Host ""
Ok "安装完成 — 可把 $VENV 理解成「本机专用打包环境」"
Info "  以后启动: $shim start"
Info "  浏览器:   http://127.0.0.1:$TAKTON_PORT"
Write-Host ""
Info "说明: 不能把别人电脑上的 venv 直接拷贝使用；本脚本会在本机自动生成等价环境。"
Write-Host ""

if ($TAKTON_NO_START -eq "1") { exit 0 }

Info "启动中..."
Get-Content $envFile | ForEach-Object {
  if ($_ -match '^\s*#' -or $_ -notmatch '=') { return }
  $k, $v = $_.Split('=', 2)
  Set-Item -Path "Env:$k" -Value $v
}
Start-Process "http://127.0.0.1:$TAKTON_PORT" -ErrorAction SilentlyContinue
& $python -m backend.cli start --host 127.0.0.1 --port $TAKTON_PORT
