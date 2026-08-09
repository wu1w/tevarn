# T5 + Electron 加深：便携分发 + vendor/ 同步（供 package.json extraResources）
# 不杀进程、不改系统服务；请先自行 cargo build -p tevarn-kernel-host --release

param(
    [string]$OutDir = "dist/portable-kernel",
    [string]$HostBin = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not $HostBin) {
    $candidates = @(
        "target\release\tevarn-kernel-host.exe",
        "target\debug\tevarn-kernel-host.exe",
        "target\release\tevarn-kernel-host",
        "target\debug\tevarn-kernel-host"
    )
    foreach ($c in $candidates) {
        $p = Join-Path $Root $c
        if (Test-Path $p) { $HostBin = $p; break }
    }
}

if (-not $HostBin -or -not (Test-Path $HostBin)) {
    Write-Error "host binary not found. Build first: cargo build -p tevarn-kernel-host"
}

$out = Join-Path $Root $OutDir
New-Item -ItemType Directory -Force -Path $out | Out-Null
Copy-Item -Force $HostBin (Join-Path $out (Split-Path $HostBin -Leaf))

$readme = @"
# Tevarn Kernel Host (portable)

## Run

``````
# Windows
.\tevarn-kernel-host.exe

# Linux/macOS
./tevarn-kernel-host
``````

Default listen: 127.0.0.1:17890

## Point backend at this binary

``````
set TEVARN_KERNEL_HOST_BIN=%CD%\tevarn-kernel-host.exe
set TEVARN_KERNEL_BACKEND=rust
set TEVARN_KERNEL_AUTO_START=1
``````

Do not kill system processes; stop a previous host only if you own that process.
"@
Set-Content -Path (Join-Path $out "README.md") -Value $readme -Encoding UTF8

# ABI hint from docs if present
$abi = Join-Path $Root "docs\kernel-abi-v1.md"
if (Test-Path $abi) {
    Copy-Item -Force $abi (Join-Path $out "kernel-abi-v1.md")
}

# 同步到 vendor/ 供 Electron extraResources 打包（不覆盖正在运行的进程文件若占用则跳过）
$vendor = Join-Path $Root "vendor\tevarn-kernel-host"
New-Item -ItemType Directory -Force -Path $vendor | Out-Null
$leaf = Split-Path $HostBin -Leaf
try {
    Copy-Item -Force (Join-Path $out $leaf) (Join-Path $vendor $leaf)
    Copy-Item -Force (Join-Path $out "README.md") (Join-Path $vendor "README.md") -ErrorAction SilentlyContinue
    Write-Host "Also copied to vendor/tevarn-kernel-host for Electron extraResources"
} catch {
    Write-Warning "vendor copy skipped (file may be in use): $_"
}

Write-Host "OK packed to $out"
Get-ChildItem $out | Format-Table Name, Length
