# Deploy website/ to Aliyun OSS for mainland-accessible static hosting (default OSS domain, no ICP).
# Requires: ossutil in PATH, or set OSSUTIL to full path.
#
#   $env:OSS_BUCKET = "tevarn-web"
#   $env:OSS_ENDPOINT = "oss-cn-hangzhou.aliyuncs.com"
#   $env:ALIYUN_ACCESS_KEY_ID = "..."
#   $env:ALIYUN_ACCESS_KEY_SECRET = "..."
#   .\scripts\deploy-china-oss.ps1

[CmdletBinding()]
param(
  [string]$Bucket = $env:OSS_BUCKET,
  [string]$Endpoint = $(if ($env:OSS_ENDPOINT) { $env:OSS_ENDPOINT } else { "oss-cn-hangzhou.aliyuncs.com" }),
  [string]$WebsiteDir = ""
)

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
if (-not $WebsiteDir) { $WebsiteDir = Join-Path $root "website" }
if (-not $Bucket) { throw "Set OSS_BUCKET (and AK env vars)." }

$ossutil = $env:OSSUTIL
if (-not $ossutil) {
  $cmd = Get-Command ossutil -ErrorAction SilentlyContinue
  if ($cmd) { $ossutil = $cmd.Source }
}
if (-not $ossutil) { throw "ossutil not found. Install: https://help.aliyun.com/document_detail/120075.html" }

$env:OSS_ACCESS_KEY_ID = $env:ALIYUN_ACCESS_KEY_ID
$env:OSS_ACCESS_KEY_SECRET = $env:ALIYUN_ACCESS_KEY_SECRET

Write-Host "Upload $WebsiteDir -> oss://$Bucket/ (endpoint $Endpoint)"
& $ossutil cp -r "$WebsiteDir/" "oss://$Bucket/" -e $Endpoint --update --force
if ($LASTEXITCODE -ne 0) { throw "ossutil failed: $LASTEXITCODE" }

Write-Host ""
Write-Host "Done. Public URL (enable static website + public-read on bucket):"
Write-Host "  https://$Bucket.$Endpoint/index.html"
Write-Host "  https://$Bucket.$Endpoint/"
