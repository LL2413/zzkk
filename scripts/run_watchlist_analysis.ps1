# One-shot watchlist data collection + enrichment + validation + report.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\run_watchlist_analysis.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\run_watchlist_analysis.ps1 -NoFetch
#   powershell -ExecutionPolicy Bypass -File scripts\run_watchlist_analysis.ps1 -DateTag 20260522
#   powershell -ExecutionPolicy Bypass -File scripts\run_watchlist_analysis.ps1 -Force

[CmdletBinding()]
param(
  [switch]$NoFetch,
  [switch]$NoEnrich,
  [switch]$Force,
  [switch]$RequireStrongFundFlow,
  [string]$DateTag = $null,
  [string]$Python = $null,
  [string]$Output = $null
)

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Resolve-Python {
  param([string]$explicit)
  if ($explicit -and (Test-Path $explicit)) { return $explicit }
  $candidates = @(
    (Join-Path $root '.venv\Scripts\python.exe'),
    "$env:USERPROFILE\.venv\Scripts\python.exe",
    "$env:USERPROFILE\.venv\Scripts\python"
  )
  foreach ($p in $candidates) { if (Test-Path $p) { return $p } }
  $which = Get-Command python -ErrorAction SilentlyContinue
  if ($which) { return $which.Source }
  throw "No Python interpreter found. Create a venv first."
}

function Latest-DataTag {
  $latest = Get-ChildItem -Path (Join-Path $root 'data') -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match '^\d{8}$' } |
    Sort-Object Name |
    Select-Object -Last 1
  if (-not $latest) { throw "No data/YYYYMMDD directories found." }
  return $latest.Name
}

$py = Resolve-Python -explicit $Python
Write-Host "python: $py"
Write-Host "cwd:    $root"

if (-not $NoFetch -and -not $DateTag) {
  $dow = (Get-Date).DayOfWeek
  if (-not $Force -and ($dow -eq 'Saturday' -or $dow -eq 'Sunday')) {
    $DateTag = Latest-DataTag
    Write-Host "weekend ($dow): skip live fetch, analyze latest data/$DateTag. Pass -Force to fetch anyway."
  } else {
    Write-Host "fetching today's watchlist data ..."
    $fetchArgs = @{ Refresh = $true; Python = $py }
    & (Join-Path $PSScriptRoot 'fetch_all.ps1') @fetchArgs
    if ($LASTEXITCODE -ne 0) {
      Write-Error "fetch_all failed (exit=$LASTEXITCODE). Stop before analysis."
      exit 5
    }
    $DateTag = Get-Date -Format 'yyyyMMdd'
  }
}

if (-not $DateTag) {
  $DateTag = Latest-DataTag
}

$dataDir = Join-Path $root "data\$DateTag"
if (-not (Test-Path $dataDir)) {
  Write-Error "Data dir not found: $dataDir"
  exit 2
}

if (-not $NoEnrich) {
  Write-Host "running enrich_all for $DateTag ..."
  & (Join-Path $PSScriptRoot 'enrich_all.ps1') $DateTag
  if ($LASTEXITCODE -ne 0) {
    Write-Error "enrich_all failed (exit=$LASTEXITCODE)."
    exit 6
  }
}

$validator = Join-Path $root '.claude\skills\china-stock-analysis\scripts\validate_data.py'
Write-Host "validating data/$DateTag ..."
& $py $validator --data-dir "data\$DateTag"
if ($LASTEXITCODE -ne 0) {
  Write-Error "validation failed (exit=$LASTEXITCODE)."
  exit 7
}

if (-not $Output) {
  $Output = Join-Path $root ("reports\watchlist_{0}.md" -f $DateTag)
}
Write-Host "generating report ..."
$analyzeArgs = @('scripts\analyze_watchlist.py', '--date', $DateTag, '--output', $Output)
if ($RequireStrongFundFlow) { $analyzeArgs += '--require-strong-fund-flow' }
& $py @analyzeArgs
if ($LASTEXITCODE -ne 0) {
  Write-Error "report generation failed (exit=$LASTEXITCODE)."
  exit 8
}

Write-Host ""
Write-Host "done: $Output"
exit 0
