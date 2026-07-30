# Phase 5.1b — 源码开发者 bootstrap（Windows）
# 用法: powershell -ExecutionPolicy Bypass -File scripts/bootstrap_dev.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

Write-Host "[takton] root=$Root"
if (-not (Test-Path ".venv\Scripts\python.exe")) {
  Write-Host "[takton] creating .venv"
  py -3 -m venv .venv
}
& .\.venv\Scripts\python.exe -m pip install -U pip
if (Test-Path "pyproject.toml") {
  & .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
} elseif (Test-Path "backend\requirements.txt") {
  & .\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
}
if (Test-Path "frontend\package.json") {
  Push-Location frontend
  npm.cmd install
  Pop-Location
}
Write-Host "[takton] bootstrap done. Next: .\.venv\Scripts\python.exe start.py"
Write-Host "[takton] version: $((Get-Content backend\VERSION -Raw).Trim())"
