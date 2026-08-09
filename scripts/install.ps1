# Tevarn one-click installer (Windows) - installs desktop client (Setup.exe)
#   iex ((irm https://raw.githubusercontent.com/wu1w/tevarn/main/scripts/install.ps1) -replace '^﻿','')
#
# Downloads Tevarn-Setup from the latest GitHub Release (or TEVARN_RELEASE_TAG) and runs NSIS.
# Note: file must stay UTF-8 without BOM for Windows PowerShell irm|iex (PS 5.1 safe).
# Does NOT set up a separate "web-only" server stack.

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Repo = if ($env:TEVARN_REPO) { $env:TEVARN_REPO } else { "wu1w/tevarn" }
if ($Repo -match "github\.com[:/](?<o>[^/]+)/(?<n>[^/.]+)") {
  $Repo = "$($Matches.o)/$($Matches.n)"
}
$TagOverride = $env:TEVARN_RELEASE_TAG
$AssetOverride = $env:TEVARN_SETUP_ASSET
$NoStart = $env:TEVARN_NO_START -eq "1"

function Write-Info([string]$m) { Write-Host "[tevarn] $m" }
function Write-Ok([string]$m) { Write-Host "[tevarn] OK $m" -ForegroundColor Green }
function Write-Err([string]$m) { Write-Host "[tevarn] ERROR: $m" -ForegroundColor Red }

function Get-LatestSetup {
  param([string]$Repository, [string]$Tag, [string]$AssetName)
  $headers = @{
    "User-Agent" = "tevarn-install.ps1"
    "Accept"     = "application/vnd.github+json"
  }
  if ($Tag) {
    $api = "https://api.github.com/repos/$Repository/releases/tags/$Tag"
  } else {
    $api = "https://api.github.com/repos/$Repository/releases/latest"
  }
  Write-Info "Resolving release via $api"
  $rel = Invoke-RestMethod -Uri $api -Headers $headers -UseBasicParsing
  $tagName = [string]$rel.tag_name
  $assets = @($rel.assets)
  if ($AssetName) {
    $hit = $assets | Where-Object { $_.name -eq $AssetName } | Select-Object -First 1
  } else {
    # Prefer current naming; never hardcode an old patch version.
    $hit = $assets | Where-Object { $_.name -match '^Tevarn-Setup-.*\.exe$' } | Select-Object -First 1
    if (-not $hit) {
      $hit = $assets | Where-Object { $_.name -match '^Tevarn Setup .*\.exe$' } | Select-Object -First 1
    }
  }
  if (-not $hit) {
    throw "No Setup.exe asset on release $tagName. Assets: $(($assets | ForEach-Object { $_.name }) -join ', ')"
  }
  [pscustomobject]@{
    Tag      = $tagName
    Name     = [string]$hit.name
    Url      = [string]$hit.browser_download_url
    Size     = [int64]$hit.size
  }
}

Write-Host ""
Write-Host "Tevarn desktop client - one-click install" -ForegroundColor Cyan
Write-Host ""

$setupMeta = $null
try {
  $setupMeta = Get-LatestSetup -Repository $Repo -Tag $TagOverride -AssetName $AssetOverride
} catch {
  Write-Info "API resolve failed ($($_.Exception.Message)); falling back to v0.3.1 asset names"
  $fallbackTag = if ($TagOverride) { $TagOverride } else { "v0.3.1" }
  $fallbackAsset = if ($AssetOverride) { $AssetOverride } else { "Tevarn-Setup-0.3.1.exe" }
  $setupMeta = [pscustomobject]@{
    Tag  = $fallbackTag
    Name = $fallbackAsset
    Url  = "https://github.com/$Repo/releases/download/$fallbackTag/$fallbackAsset"
    Size = 0
  }
}

Write-Ok "Release $($setupMeta.Tag) → $($setupMeta.Name)"

$work = Join-Path $env:TEMP ("tevarn-setup-" + [guid]::NewGuid().ToString("n").Substring(0, 8))
New-Item -ItemType Directory -Force -Path $work | Out-Null
$setupPath = Join-Path $work $setupMeta.Name

$urls = @(
  $setupMeta.Url
  "https://github.com/$Repo/releases/latest/download/$($setupMeta.Name)"
  "https://github.com/$Repo/releases/download/$($setupMeta.Tag)/$($setupMeta.Name)"
) | Select-Object -Unique

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
  Write-Err "Open: https://github.com/$Repo/releases and download $($setupMeta.Name) manually."
  exit 1
}

Write-Info "Running installer (one-click NSIS)..."
$p = Start-Process -FilePath $setupPath -ArgumentList @("/S") -Wait -PassThru
if ($null -ne $p.ExitCode -and $p.ExitCode -ne 0) {
  Write-Info "Silent install exit $($p.ExitCode), trying interactive..."
  $p2 = Start-Process -FilePath $setupPath -Wait -PassThru
  if ($null -ne $p2.ExitCode -and $p2.ExitCode -ne 0) {
    Write-Err "Installer failed with exit code $($p2.ExitCode)"
    exit $p2.ExitCode
  }
}

Write-Ok "Client installed ($($setupMeta.Tag))"

$pf86 = ${env:ProgramFiles(x86)}
$candidates = @(
  (Join-Path $env:LOCALAPPDATA "Programs\Tevarn\Tevarn.exe"),
  (Join-Path $env:LOCALAPPDATA "Programs\tevarn\Tevarn.exe"),
  (Join-Path $env:ProgramFiles "Tevarn\Tevarn.exe")
)
if ($pf86) {
  $candidates += (Join-Path $pf86 "Tevarn\Tevarn.exe")
}
$exe = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $exe) {
  $desk = Join-Path ([Environment]::GetFolderPath("Desktop")) "Tevarn.lnk"
  if (Test-Path $desk) {
    Write-Ok "Shortcut on Desktop: $desk"
  } else {
    Write-Info "Installer finished. Open Tevarn from Start Menu if the window did not open."
  }
} else {
  Write-Ok "Found: $exe"
  if (-not $NoStart) {
    Write-Info "Launching Tevarn..."
    Start-Process -FilePath $exe
  }
}

Write-Host ""
Write-Ok "Done. Use the Tevarn desktop app."
Write-Host ""
