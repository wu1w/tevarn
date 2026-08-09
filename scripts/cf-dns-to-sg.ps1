# Point tevarn.com DNS to Singapore origin (grey cloud).
# Requires a Cloudflare API Token with:
#   Permissions: Zone.DNS Edit, Zone.Zone Read
#   Zone Resources: Include → Specific zone → tevarn.com
#
# Usage:
#   $env:CLOUDFLARE_API_TOKEN = "paste_new_token_here"
#   .\scripts\cf-dns-to-sg.ps1

param(
  [string]$Token = $env:CLOUDFLARE_API_TOKEN,
  [string]$ZoneId = "17914e7d0e30e9a28916b2ca5f3274d2",
  [string]$OriginIp = "45.77.170.214",
  [string]$AccountId = "859f67cd00473260380bf541b95eb4ad"
)

$ErrorActionPreference = "Stop"
if (-not $Token) { throw "Set CLOUDFLARE_API_TOKEN first (Zone.DNS Edit required)." }

$headers = @{
  Authorization  = "Bearer $Token"
  "Content-Type" = "application/json"
}

function Invoke-Cf($Method, $Uri, $BodyObj = $null) {
  $params = @{ Method = $Method; Uri = $Uri; Headers = $headers; UseBasicParsing = $true }
  if ($null -ne $BodyObj) { $params.Body = ($BodyObj | ConvertTo-Json -Compress) }
  try {
    return Invoke-RestMethod @params
  } catch {
    $resp = $_.Exception.Response
    if ($resp) {
      $reader = New-Object System.IO.StreamReader($resp.GetResponseStream())
      throw "CF $Method $Uri failed: $($reader.ReadToEnd())"
    }
    throw
  }
}

Write-Host "1) Probe DNS list permission..."
$probe = Invoke-Cf GET "https://api.cloudflare.com/client/v4/zones/$ZoneId/dns_records?per_page=5"
if (-not $probe.success) { throw "Token cannot list DNS records. Create a User API Token with Zone.DNS Edit on tevarn.com." }
Write-Host "   OK ($($probe.result.Count) sample records)"

Write-Host "2) Detach Workers custom domains (if any)..."
$wd = Invoke-Cf GET "https://api.cloudflare.com/client/v4/accounts/$AccountId/workers/domains"
foreach ($d in @($wd.result)) {
  if (-not $d) { continue }
  Write-Host "   delete $($d.hostname)"
  Invoke-Cf DELETE "https://api.cloudflare.com/client/v4/accounts/$AccountId/workers/domains/$($d.id)" | Out-Null
}

Write-Host "3) Remove existing A/AAAA/CNAME for apex/www/dl..."
$all = Invoke-Cf GET "https://api.cloudflare.com/client/v4/zones/$ZoneId/dns_records?per_page=100"
$names = @("tevarn.com", "www.tevarn.com", "dl.tevarn.com")
foreach ($r in @($all.result)) {
  if ($names -contains $r.name -and $r.type -in @("A", "AAAA", "CNAME")) {
    Write-Host "   delete $($r.type) $($r.name) -> $($r.content)"
    Invoke-Cf DELETE "https://api.cloudflare.com/client/v4/zones/$ZoneId/dns_records/$($r.id)" | Out-Null
  }
}

Write-Host "4) Create grey-cloud A records -> $OriginIp ..."
foreach ($name in @("tevarn.com", "www", "dl")) {
  $body = @{ type = "A"; name = $name; content = $OriginIp; ttl = 120; proxied = $false }
  $created = Invoke-Cf POST "https://api.cloudflare.com/client/v4/zones/$ZoneId/dns_records" $body
  Write-Host "   $($created.result.name) proxied=$($created.result.proxied) -> $($created.result.content)"
}

Write-Host ""
Write-Host "Done. Wait 1-3 min then:"
Write-Host "  nslookup tevarn.com 1.1.1.1"
Write-Host "  curl -I http://tevarn.com/"
Write-Host "Expect A = $OriginIp and HTTP 200 from Singapore nginx."
