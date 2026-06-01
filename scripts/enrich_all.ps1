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
  [string]$DateTag = $null,
  [string]$Python = $null
)

$ErrorActionPreference = 'Continue'
. (Join-Path $PSScriptRoot 'workflow_common.ps1')
Set-Location $workflowRepoRoot

try {
  $py = Resolve-WorkflowPython -Explicit $Python
} catch {
  Write-Error $_.Exception.Message
  exit 2
}

$dateArgs = @()
if ($DateTag) { $dateArgs = @('--date', $DateTag) }

$enrichers = @(
  @('basic_info (historical + hardcoded)', 'scripts\enrich_basic_info.py'),
  @('SZ 净融资 (balance delta)', 'scripts\enrich_margin_net.py'),
  @('EM 分档主力资金 (强口径，超大单+大单)', 'scripts\enrich_em_fund_flow.py'),
  @('个股资金流 fallback (THS same-day net)', 'scripts\enrich_fund_flow_fallback.py'),
  @('板块资金流 (watchlist aggregation)', 'scripts\enrich_sector_flow.py'),
  @('日频估值 fallback (price-adjusted + explicit source)', 'scripts\enrich_daily_valuation_fallback.py'),
  @('估值口径 (PE / PB / PS)', 'scripts\enrich_valuation.py'),
  @('主力流向连续天数 (streak)', 'scripts\enrich_streak.py'),
  @('价主背离 (divergence)', 'scripts\enrich_divergence.py'),
  @('个股 vs 板块 (alpha)', 'scripts\enrich_alpha.py'),
  @('三维信号评分 (score)', 'scripts\enrich_score.py')
)

$failed = @()
for ($i = 0; $i -lt $enrichers.Count; $i++) {
  $label = $enrichers[$i][0]
  $script = $enrichers[$i][1]
  Write-Host ""
  Write-Host ("=== {0}. {1} ===" -f ($i + 1), $label)
  if (-not (Test-Path $script)) {
    Write-Error "Missing enricher: $script"
    $failed += $script
    continue
  }
  & $py $script @dateArgs
  if ($LASTEXITCODE -ne 0) {
    Write-Error "Enricher failed (exit=$LASTEXITCODE): $script"
    $failed += $script
  }
}

Write-Host ""
if ($failed.Count -gt 0) {
  Write-Error "Enrichment failed: $($failed -join ', ')"
  exit 1
}
Write-Host "All enrichments complete."
exit 0
