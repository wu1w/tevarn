# Build takton-kernel-host (Windows MSVC) and stage vendor copy for product discovery.
# Usage: .\scripts\build-kernel-host.ps1 [-Release]

param(
    [switch]$Release
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$cargo = $null
foreach ($c in @(
    (Join-Path $env:USERPROFILE ".cargo\bin\cargo.exe"),
    (Join-Path $env:USERPROFILE ".cargo\bin\cargo"),
    "cargo"
)) {
    if ($c -eq "cargo") {
        $cmd = Get-Command cargo -ErrorAction SilentlyContinue
        if ($cmd) { $cargo = $cmd.Source; break }
    } elseif (Test-Path $c) {
        $cargo = $c
        break
    }
}
if (-not $cargo) {
    throw "cargo not found. Install Rust: https://rustup.rs"
}

$vsDevCmd = "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\Common7\Tools\VsDevCmd.bat"
if (-not (Test-Path $vsDevCmd)) {
    $vsDevCmd = "C:\Program Files\Microsoft Visual Studio\2022\BuildTools\Common7\Tools\VsDevCmd.bat"
}

$profileFlag = if ($Release) { "--release" } else { "" }
$cmd = "`"$cargo`" build -p takton-kernel-host $profileFlag"

if (Test-Path $vsDevCmd) {
    cmd /c "call `"$vsDevCmd`" -arch=amd64 && $cmd"
} else {
    Write-Warning "VsDevCmd.bat not found; trying cargo directly (needs MSVC link.exe on PATH)"
    $args = @("build", "-p", "takton-kernel-host")
    if ($Release) { $args += "--release" }
    & $cargo @args
    if ($LASTEXITCODE -ne 0) { throw "cargo build failed: $LASTEXITCODE" }
}

$built = if ($Release) {
    Join-Path $Root "target\release\takton-kernel-host.exe"
} else {
    Join-Path $Root "target\debug\takton-kernel-host.exe"
}
if (-not (Test-Path $built)) {
    # non-windows name
    $builtAlt = if ($Release) {
        Join-Path $Root "target\release\takton-kernel-host"
    } else {
        Join-Path $Root "target\debug\takton-kernel-host"
    }
    if (Test-Path $builtAlt) { $built = $builtAlt }
}

if (-not (Test-Path $built)) {
    throw "build finished but binary missing under target/"
}

# Stage for product discovery (Electron extraResources + start.py fallback)
$vendorDir = Join-Path $Root "vendor\takton-kernel-host"
New-Item -ItemType Directory -Force -Path $vendorDir | Out-Null
$destName = Split-Path $built -Leaf
$dest = Join-Path $vendorDir $destName
Copy-Item -Force $built $dest
$staged = @{
    staged_at = (Get-Date).ToUniversalTime().ToString("o")
    source    = $built
    dest      = $dest
    profile   = $(if ($Release) { "release" } else { "debug" })
    note      = "Binary is gitignored (*.exe); rebuild before pack/dist"
} | ConvertTo-Json
Set-Content -Path (Join-Path $vendorDir "STAGED.json") -Value $staged -Encoding utf8
Write-Host "OK: $built"
Write-Host "OK: staged $dest"
Write-Host "ABI: docs/kernel-abi-v1.md"
