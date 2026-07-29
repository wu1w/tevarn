# Kernel-first host (no UI required)
# Usage: from repo root
#   .\scripts\start-kernel-host.ps1
#   .\scripts\start-kernel-host.ps1 -Port 8090

param(
  [string]$HostAddr = "127.0.0.1",
  [int]$Port = 8090
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root
$env:PYTHONPATH = $Root
if (-not $env:TAKTON_AIOS_PROFILE) { $env:TAKTON_AIOS_PROFILE = "aios-dev" }
$env:TAKTON_SINGLE_USER_MODE = "true"

Write-Host "[takton] Kernel Host → http://${HostAddr}:${Port}"
Write-Host "[takton] Console may connect later. Close UI ≠ stop this process."
python -m backend.runtime --host $HostAddr --port $Port
