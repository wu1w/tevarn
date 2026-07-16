# Takton one-line installer for Windows (PowerShell)
# 小白用法（复制到 PowerShell）：
#   irm https://raw.githubusercontent.com/wu1w/takton/main/scripts/install.ps1 | iex
#
# 可选环境变量：
#   $env:TAKTON_HOME = "$env:USERPROFILE\.takton"
#   $env:TAKTON_PORT = "8090"
#   $env:TAKTON_NO_START = "1"          # 只安装不启动
#   $env:TAKTON_SOURCE = "D:\path\to\takton"

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$TAKTON_HOME = if ($env:TAKTON_HOME) { $env:TAKTON_HOME } else { Join-Path $env:USERPROFILE ".takton" }
$TAKTON_REPO = if ($env:TAKTON_REPO) { $env:TAKTON_REPO } else { "https://github.com/wu1w/takton.git" }
$TAKTON_REF  = if ($env:TAKTON_REF)  { $env:TAKTON_REF }  else { "main" }
$TAKTON_PORT = if ($env:TAKTON_PORT) { $env:TAKTON_PORT } else { "8090" }
$TAKTON_NO_START = if ($env:TAKTON_NO_START) { $env:TAKTON_NO_START } else { "0" }
$VENV = Join-Path $TAKTON_HOME "venv"
$SRC  = Join-Path $TAKTON_HOME "src"

function Info([string]$m) { Write-Host "[takton] $m" -ForegroundColor Gray }
function Ok([string]$m)   { Write-Host "[takton] ✓ $m" -ForegroundColor Green }
function Die([string]$m)  { Write-Host "[takton] ERROR: $m" -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "  Takton 一键安装 (Windows)" -ForegroundColor Cyan
Write-Host "  接下来会自动：选 Python → 下载代码 → 装依赖 → 自检 → 启动" -ForegroundColor DarkGray
Write-Host ""

function Get-PythonCmd {
  # Prefer 3.11/3.12/3.13 — 3.14 breaks many wheels (pydantic-core)
  $candidates = New-Object System.Collections.Generic.List[string]
  if (Get-Command py -ErrorAction SilentlyContinue) {
    foreach ($v in @("3.12", "3.11", "3.13", "3.10")) {
      try {
        $out = & py "-$v" -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) { [void]$candidates.Add($out.Trim()) }
      } catch {}
    }
  }
  foreach ($c in @("python3.12", "python3.11", "python3.13", "python3.10", "python3", "python")) {
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
  $prog = Join-Path $env:LOCALAPPDATA "Programs\Python"
  if (Test-Path $prog) {
    foreach ($v in @("Python312", "Python311", "Python313", "Python310")) {
      $p = Join-Path $prog "$v\python.exe"
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

# --- preflight ---
$pyInfo = Get-PythonCmd
if (-not $pyInfo) {
  Die @"
找不到可用的 Python 3.10–3.13（推荐 3.11 或 3.12）。

请先安装其一（安装时勾选 Add python.exe to PATH）：
  https://www.python.org/downloads/release/python-31210/

若已装过 3.14，请再装一个 3.11/3.12，本脚本会自动选对的版本。
"@
}
$pyExe = $pyInfo.Exe
Ok "Python $($pyInfo.Ver) → $pyExe"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  Die "需要 Git。请安装：https://git-scm.com/download/win （装完重新打开 PowerShell）"
}
Ok "Git 已就绪"

New-Item -ItemType Directory -Force -Path $TAKTON_HOME | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $TAKTON_HOME "data") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $TAKTON_HOME "data\uploads") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $TAKTON_HOME "data\workspace") | Out-Null

# --- source ---
if ($env:TAKTON_SOURCE) {
  $SRC = (Resolve-Path $env:TAKTON_SOURCE).Path
  Info "使用本地源码: $SRC"
} elseif ((Test-Path ".\backend\main.py") -and (Test-Path ".\pyproject.toml")) {
  $SRC = (Resolve-Path ".").Path
  Info "使用当前目录源码: $SRC"
} else {
  if (Test-Path (Join-Path $SRC ".git")) {
    Info "更新源码 $SRC ($TAKTON_REF) ..."
    git -C $SRC fetch --depth 1 origin $TAKTON_REF
    if ($LASTEXITCODE -ne 0) { Die "git fetch 失败，请检查网络" }
    git -C $SRC checkout -q FETCH_HEAD
  } else {
    Info "正在从 GitHub 下载 Takton（首次约 1–3 分钟）..."
    if (Test-Path $SRC) { Remove-Item -Recurse -Force $SRC }
    git clone --depth 1 --branch $TAKTON_REF $TAKTON_REPO $SRC 2>$null
    if ($LASTEXITCODE -ne 0) {
      git clone --depth 1 $TAKTON_REPO $SRC
    }
    if ($LASTEXITCODE -ne 0) { Die "git clone 失败。请确认能访问 github.com" }
  }
  Ok "源码就绪"
}

if (-not (Test-Path (Join-Path $SRC "backend\main.py"))) {
  Die "源码不完整: 缺少 backend\main.py"
}
if (-not (Test-Path (Join-Path $SRC "backend\static\index.html"))) {
  Info "警告: 未找到预构建前端 backend/static（仓库应已包含）。仍可启动 API。"
}

# --- venv + deps ---
Info "创建独立虚拟环境（不影响系统其它 Python 项目）..."
if (Test-Path $VENV) { Remove-Item -Recurse -Force $VENV }
& $pyExe -m venv $VENV --clear
$python = Join-Path $VENV "Scripts\python.exe"
if (-not (Test-Path $python)) { Die "venv 创建失败: $python" }

# Isolate from ambient PYTHONPATH / other venvs (critical on machines with Hermes etc.)
$env:VIRTUAL_ENV = $VENV
$env:PYTHONPATH = ""
$env:PYTHONHOME = ""
$env:PYTHONNOUSERSITE = "1"
$env:PATH = "$(Join-Path $VENV 'Scripts');$env:PATH"

Info "升级 pip..."
& $python -m pip install -U pip setuptools wheel -q
if ($LASTEXITCODE -ne 0) { Die "pip 升级失败" }

Info "安装运行依赖（可能需要几分钟，请保持网络畅通）..."
$prodReq = Join-Path $SRC "backend\requirements-prod.txt"
$fallbackReq = Join-Path $SRC "backend\requirements.txt"
if (Test-Path $prodReq) {
  & $python -m pip install -r $prodReq -q
  if ($LASTEXITCODE -ne 0) {
    Info "prod 依赖安装失败，尝试 requirements.txt ..."
    if (Test-Path $fallbackReq) {
      & $python -m pip install -r $fallbackReq -q
    }
  }
} elseif (Test-Path $fallbackReq) {
  & $python -m pip install -r $fallbackReq -q
}
if ($LASTEXITCODE -ne 0) { Die "依赖安装失败。请检查网络/代理后重试。" }

Info "安装 Takton 本体..."
& $python -m pip install -e $SRC -q
if ($LASTEXITCODE -ne 0) { Die "pip install -e 失败" }

Info "自检关键模块..."
& $python -c "import fastapi, uvicorn, sqlalchemy, aiosqlite, httpx, jose, backend.main; print('import_ok')"
if ($LASTEXITCODE -ne 0) {
  Die "依赖导入失败。常见原因：选到了 Python 3.14。请安装 3.11/3.12 后重跑本脚本。"
}
Ok "依赖自检通过"

# --- env ---
$envFile = Join-Path $TAKTON_HOME ".env"
if (-not (Test-Path $envFile)) {
  Info "生成本地配置与密钥..."
  $jwt = & $python -c "import secrets; print(secrets.token_hex(32))"
  $api = & $python -c "import secrets; print(secrets.token_hex(32))"
  $salt = & $python -c "import secrets; print(secrets.token_hex(16))"
  $dbPath = (Join-Path $TAKTON_HOME "data\takton.db") -replace '\\','/'
  $up = (Join-Path $TAKTON_HOME "data\uploads") -replace '\\','/'
  $ws = (Join-Path $TAKTON_HOME "data\workspace") -replace '\\','/'
  @"
# Auto-generated by Takton installer — do not commit
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
  Ok "配置已写入 $envFile"
}

# --- launcher ---
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
  for /f "usebackq tokens=1,* delims==" %%A in ("$envFile") do (
    if not "%%A"=="" if not "%%A:~0,1%"=="#" set "%%A=%%B"
  )
)
"$python" -m backend.cli %*
"@ | Set-Content -Path $shim -Encoding ASCII

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$binDir*") {
  try {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$binDir", "User")
    $env:Path = "$env:Path;$binDir"
    Info "已把 takton 加入用户 PATH（新开终端后可直接打 takton）"
  } catch {
    Info "无法自动改 PATH。以后请用: $shim"
  }
}

Write-Host ""
Ok "安装完成"
Info "  源码:  $SRC"
Info "  启动:  $shim start --port $TAKTON_PORT"
Info "  浏览器: http://127.0.0.1:$TAKTON_PORT"
Info "  配置:  $envFile"
Write-Host ""

if ($TAKTON_NO_START -eq "1") {
  Info "TAKTON_NO_START=1，不自动启动。"
  exit 0
}

Info "正在启动 Takton..."
# Load env for child
Get-Content $envFile | ForEach-Object {
  if ($_ -match '^\s*#' -or $_ -notmatch '=') { return }
  $k, $v = $_.Split('=', 2)
  Set-Item -Path "Env:$k" -Value $v
}
Start-Process "http://127.0.0.1:$TAKTON_PORT" -ErrorAction SilentlyContinue
& $python -m backend.cli start --host 127.0.0.1 --port $TAKTON_PORT
