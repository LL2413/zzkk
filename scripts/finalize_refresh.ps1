# Deterministic post-fetch pipeline:
# enrich -> validate -> signal-score post-flight -> Markdown report.

[CmdletBinding()]
param(
  [string]$DateTag = (Get-Date -Format 'yyyyMMdd'),
  [string]$Python = $null,
  [switch]$RequireStrongFundFlow,
  [switch]$NoReport
)

$ErrorActionPreference = 'Continue'
. (Join-Path $PSScriptRoot 'workflow_common.ps1')
Set-Location $workflowRepoRoot

try {
  Assert-WorkflowDateTag -DateTag $DateTag
  $py = Resolve-WorkflowPython -Explicit $Python
  $watchlist = @(Get-WorkflowWatchlistSymbols)
} catch {
  Write-Error $_.Exception.Message
  exit 2
}

$dataDir = Join-Path $workflowRepoRoot "data\$DateTag"
if (-not (Test-Path $dataDir)) {
  Write-Error "Data directory not found: $dataDir"
  exit 2
}

Write-Host "=== finalize refresh: $DateTag ==="
Write-Host "python: $py"
Write-Host "data:   $dataDir"

$unusable = @(Get-WorkflowUnusableSnapshotSymbols -DataDir $dataDir -Symbols $watchlist)
if ($unusable.Count -gt 0) {
  Write-Error "watchlist snapshot pre-flight failed; missing or unreadable: $($unusable -join ' ')"
  exit 4
}

Write-Host ""
Write-Host "running enrichers ..."
& (Join-Path $PSScriptRoot 'enrich_all.ps1') -DateTag $DateTag -Python $py
if ($LASTEXITCODE -ne 0) {
  Write-Error "enrich_all failed (exit=$LASTEXITCODE)"
  exit 3
}

$validator = Join-Path $workflowRepoRoot '.claude\skills\china-stock-analysis\scripts\validate_data.py'
if (-not (Test-Path $validator)) {
  Write-Error "Data validator missing: $validator"
  exit 4
}

Write-Host ""
Write-Host "validating data\$DateTag (expected-count=$($watchlist.Count)) ..."
& $py $validator --data-dir "data\$DateTag" --expected-count $watchlist.Count
if ($LASTEXITCODE -ne 0) {
  Write-Error "data validation failed (exit=$LASTEXITCODE)"
  exit 4
}

$missingScores = @(Get-WorkflowMissingSignalScoreSymbols -DataDir $dataDir)
if ($missingScores.Count -gt 0) {
  Write-Error "signal-score post-flight failed; missing _signal_score: $($missingScores -join ' ')"
  exit 5
}
Write-Host "signal-score post-flight: ok ($($watchlist.Count)/$($watchlist.Count))"

if (-not $NoReport) {
  $analyzer = Join-Path $workflowRepoRoot 'scripts\analyze_watchlist.py'
  $reportPath = Join-Path $workflowRepoRoot ("reports\watchlist_{0}.md" -f $DateTag)
  if (-not (Test-Path $analyzer)) {
    Write-Error "Report generator missing: $analyzer"
    exit 6
  }

  Write-Host ""
  Write-Host "generating report: $reportPath"
  $analyzeArgs = @($analyzer, '--date', $DateTag, '--output', $reportPath)
  if ($RequireStrongFundFlow) { $analyzeArgs += '--require-strong-fund-flow' }
  & $py @analyzeArgs
  if ($LASTEXITCODE -ne 0) {
    Write-Error "report generation failed (exit=$LASTEXITCODE)"
    exit 6
  }
}

Write-Host ""
Write-Host "finalize complete: data\$DateTag"
exit 0
