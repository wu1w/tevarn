# Takton one-click installer (Windows) - installs desktop client (Setup.exe)
#   iex ((irm https://raw.githubusercontent.com/wu1w/takton/main/scripts/install.ps1) -replace '^﻿','')
#
# Downloads Takton-Setup from GitHub Releases and runs NSIS installer.
# Note: file must stay UTF-8 without BOM for Windows PowerShell irm|iex (PS 5.1 safe).
# Does NOT set up a separate "web-only" server stack.

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Repo = if ($env:TAKTON_REPO) { $env:TAKTON_REPO } else { "wu1w/takton" }
# allow owner/name or full git url
if ($Repo -match "github\.com[:/](?<o>[^/]+)/(?<n>[^/.]+)") {
  $Repo = "$($Matches.o)/$($Matches.n)"
}
$Tag = if ($env:TAKTON_RELEASE_TAG) { $env:TAKTON_RELEASE_TAG } else { "v0.1.1" }
$AssetName = if ($env:TAKTON_SETUP_ASSET) { $env:TAKTON_SETUP_ASSET } else { "Takton-Setup-0.1.1.exe" }
$NoStart = $env:TAKTON_NO_START -eq "1"

function Write-Info([string]$m) { Write-Host "[takton] $m" }
function Write-Ok([string]$m) { Write-Host "[takton] OK $m" -ForegroundColor Green }
function Write-Err([string]$m) { Write-Host "[takton] ERROR: $m" -ForegroundColor Red }

Write-Host ""
Write-Host "Takton desktop client - one-click install" -ForegroundColor Cyan
Write-Host ""

$work = Join-Path $env:TEMP ("takton-setup-" + [guid]::NewGuid().ToString("n").Substring(0, 8))
New-Item -ItemType Directory -Force -Path $work | Out-Null
$setupPath = Join-Path $work $AssetName

$urls = @(
  "https://github.com/$Repo/releases/download/$Tag/$AssetName",
  "https://github.com/$Repo/releases/latest/download/$AssetName"
)

$downloaded = $false
foreach ($url in $urls) {
  try {
    Write-Info "Downloading: $url"
    Invoke-WebRequest -Uri $url -OutFile $setupPath -UseBasicParsing
    if ((Test-Path $setupPath) -and ((Get-Item $setupPath).Length -gt 1MB)) {
      $downloaded = $true
      Write-Ok ("Downloaded {0:N1} MB" -f ((Get-Item $setupPath).Length / 1MB))
      break
    }
  } catch {
    Write-Info "Retry next URL ($($_.Exception.Message))"
  }
}

if (-not $downloaded) {
  Write-Err "Failed to download client installer."
  Write-Err "Open: https://github.com/$Repo/releases and download $AssetName manually."
  exit 1
}

Write-Info "Running installer (one-click NSIS)..."
# electron-builder NSIS oneClick: running the exe is enough; /S is silent if supported
$p = Start-Process -FilePath $setupPath -ArgumentList @("/S") -Wait -PassThru
# If silent exit non-zero, retry interactive
if ($null -ne $p.ExitCode -and $p.ExitCode -ne 0) {
  Write-Info "Silent install exit $($p.ExitCode), trying interactive..."
  $p2 = Start-Process -FilePath $setupPath -Wait -PassThru
  if ($null -ne $p2.ExitCode -and $p2.ExitCode -ne 0) {
    Write-Err "Installer failed with exit code $($p2.ExitCode)"
    exit $p2.ExitCode
  }
}

Write-Ok "Client installed"

# Common install locations
$pf86 = ${env:ProgramFiles(x86)}
$candidates = @(
  (Join-Path $env:LOCALAPPDATA "Programs\Takton\Takton.exe"),
  (Join-Path $env:LOCALAPPDATA "Programs\takton\Takton.exe"),
  (Join-Path $env:ProgramFiles "Takton\Takton.exe")
)
if ($pf86) {
  $candidates += (Join-Path $pf86 "Takton\Takton.exe")
}
$exe = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $exe) {
  $desk = Join-Path ([Environment]::GetFolderPath("Desktop")) "Takton.lnk"
  if (Test-Path $desk) {
    Write-Ok "Shortcut on Desktop: $desk"
  } else {
    Write-Info "Installer finished. Open Takton from Start Menu if the window did not open."
  }
} else {
  Write-Ok "Found: $exe"
  if (-not $NoStart) {
    Write-Info "Launching Takton..."
    Start-Process -FilePath $exe
  }
}

Write-Host ""
Write-Ok "Done. Use the Takton desktop app."
Write-Host ""
