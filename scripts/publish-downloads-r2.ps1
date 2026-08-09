# Upload Tevarn installers to Cloudflare R2 for official-site downloads.
# Requires: Node.js + wrangler (npx), Cloudflare login or API token.
#
# Example:
#   .\scripts\publish-downloads-r2.ps1 -Version 0.4.0 `
#     -WinSetup "frontend\release\Tevarn-Setup-0.4.0-x64.exe" `
#     -Apk "mobile\dist\Tevarn-Mobile-0.4.0.apk"

[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$Version,

  [string]$Bucket = "tevarn-releases",

  [string]$WinSetup = "",

  [string]$Apk = "",

  [string]$CdnBase = "https://dl.tevarn.com",

  # Optional: set for CI (Account API Token with R2 edit)
  [string]$AccountId = $env:CF_ACCOUNT_ID,
  [string]$ApiToken = $env:CF_API_TOKEN
)

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $root

function Resolve-DefaultPath([string]$explicit, [string[]]$candidates) {
  if ($explicit -and (Test-Path $explicit)) { return (Resolve-Path $explicit).Path }
  foreach ($c in $candidates) {
    $p = Join-Path $root $c
    if (Test-Path $p) { return (Resolve-Path $p).Path }
  }
  return $null
}

$winPath = Resolve-DefaultPath $WinSetup @(
  "frontend\release\Tevarn-Setup-$Version-x64.exe",
  "frontend\release\Tevarn-Setup-$Version.exe"
)
$apkPath = Resolve-DefaultPath $Apk @(
  "mobile\dist\Tevarn-Mobile-$Version.apk"
)

if (-not $winPath) { throw "Windows Setup not found. Pass -WinSetup path." }
if (-not $apkPath) { throw "Android APK not found. Pass -Apk path." }

$winKey = "v$Version/$(Split-Path $winPath -Leaf)"
$apkKey = "v$Version/$(Split-Path $apkPath -Leaf)"

Write-Host "Bucket : $Bucket"
Write-Host "Win    : $winPath  ->  $winKey  ($([math]::Round((Get-Item $winPath).Length/1MB,1)) MB)"
Write-Host "APK    : $apkPath  ->  $apkKey  ($([math]::Round((Get-Item $apkPath).Length/1MB,1)) MB)"

$envArgs = @()
if ($AccountId) { $env:CLOUDFLARE_ACCOUNT_ID = $AccountId }
if ($ApiToken) { $env:CLOUDFLARE_API_TOKEN = $ApiToken }

function Invoke-R2Put([string]$file, [string]$key) {
  Write-Host "`n>> Uploading $key ..."
  # wrangler r2 object put <bucket>/<key> --file=... --remote
  $target = "$Bucket/$key"
  npx --yes wrangler@4 r2 object put $target --file=$file --remote
  if ($LASTEXITCODE -ne 0) { throw "wrangler upload failed for $key (exit $LASTEXITCODE)" }
}

Invoke-R2Put $winPath $winKey
Invoke-R2Put $apkPath $apkKey

Write-Host "`nDone. Public URLs (after custom domain is active):"
Write-Host "  $CdnBase/$winKey"
Write-Host "  $CdnBase/$apkKey"
Write-Host "`nVerify:"
Write-Host "  curl.exe -I `"$CdnBase/$winKey`""
Write-Host "  curl.exe -I `"$CdnBase/$apkKey`""
