# Run all post-fetch enrichments in sequence (Windows / PowerShell).
#
# Usage:
#   .\scripts\enrich_all.ps1                # default: data/<today>
#   .\scripts\enrich_all.ps1 20260514       # specific date
#
# Each enricher modifies snapshots in place (basic_info / valuation summary /
# SZ margin net) or writes a sidecar file (sector_flow_aggregated.json).
# All are idempotent - safe to re-run.

[CmdletBinding()]
param(
  [Parameter(Position=0)]
  [string]$DateTag = $null
)

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# Resolve python
$py = $null
$candidates = @(
  (Join-Path $root '.venv\Scripts\python.exe'),
  "$env:USERPROFILE\.venv\Scripts\python.exe"
)
foreach ($p in $candidates) { if (Test-Path $p) { $py = $p; break } }
if (-not $py) {
  $which = Get-Command python -ErrorAction SilentlyContinue
  if ($which) { $py = $which.Source }
}
if (-not $py) {
  Write-Error "No Python interpreter found. Expected venv at $env:USERPROFILE\.venv\Scripts\python.exe"
  exit 2
}

$dateArgs = @()
if ($DateTag) { $dateArgs = @('--date', $DateTag) }

Write-Host "=== 1. basic_info (historical + hardcoded) ==="
& $py scripts\enrich_basic_info.py @dateArgs

Write-Host ""
Write-Host "=== 2. SZ 净融资 (balance delta) ==="
& $py scripts\enrich_margin_net.py @dateArgs

Write-Host ""
Write-Host "=== 3. 个股资金流 fallback (THS same-day net) ==="
& $py scripts\enrich_fund_flow_fallback.py @dateArgs

Write-Host ""
Write-Host "=== 4. 板块资金流 (watchlist aggregation) ==="
& $py scripts\enrich_sector_flow.py @dateArgs

Write-Host ""
Write-Host "=== 5. 估值口径 (PE / PB / PS) ==="
& $py scripts\enrich_valuation.py @dateArgs

Write-Host ""
Write-Host "=== 6. 主力流向连续天数 (streak) ==="
& $py scripts\enrich_streak.py @dateArgs

Write-Host ""
Write-Host "=== 7. 价主背离 (divergence) ==="
& $py scripts\enrich_divergence.py @dateArgs

Write-Host ""
Write-Host "=== 8. 个股 vs 板块 (alpha) ==="
& $py scripts\enrich_alpha.py @dateArgs

Write-Host ""
Write-Host "=== 9. 三维信号评分 (score) ==="
& $py scripts\enrich_score.py @dateArgs

Write-Host ""
Write-Host "All enrichments complete."
