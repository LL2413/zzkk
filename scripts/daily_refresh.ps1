# Daily auto-refresh for china-stock-analysis data.
# Suitable for Windows Task Scheduler.
#
# What it does:
#   1. cd to repo, git pull (retry 4x on transient network failures)
#   2. Skip weekends (A-share markets closed; data unchanged)
#   3. Run fetch_all.ps1 -Refresh for all watchlist symbols
#   4. Run post-fetch enrichers (basic_info / SZ margin net / sector
#      aggregation / valuation triage) — failures are logged but
#      non-fatal so fresh fetch data still ships
#   5. If any data/<today> file changed, commit and push to the tracked branch
#   6. Log everything to logs/daily_refresh_<date>.log (under .gitignore)
#
# Register once (run PowerShell as the user you want the task to run as):
#   pwsh scripts\register_daily_task.ps1
# Or manually:
#   schtasks /Create /SC DAILY /TN "ZzkkDailyRefresh" /ST 18:00 /TR `
#     "pwsh.exe -NoProfile -ExecutionPolicy Bypass -File C:\Users\computer\zzkk\scripts\daily_refresh.ps1"
#
# Manual run (for testing):
#   pwsh scripts\daily_refresh.ps1 -Force            # ignore weekend skip
#   pwsh scripts\daily_refresh.ps1 -NoPush           # fetch + commit, don't push
#   pwsh scripts\daily_refresh.ps1 -Branch main      # override branch

[CmdletBinding()]
param(
  [switch]$Force,             # run even on weekends
  [switch]$NoPush,            # commit locally only
  [string]$Branch = 'claude/stock-market-analysis-skill-9p7lc',
  [string]$Python = $null
)

$ErrorActionPreference = 'Continue'  # don't abort on single-source network flakes
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# --- logging ---
$logDir = Join-Path $root 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir ("daily_refresh_{0}.log" -f (Get-Date -Format 'yyyyMMdd'))
function Log([string]$msg) {
  $line = '[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
  Write-Host $line
  Add-Content -Path $logFile -Value $line -Encoding UTF8
}

Log "=== daily_refresh start ==="
Log "cwd: $root"

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
  throw "No Python interpreter found. Create a venv: python -m venv .venv; .venv\Scripts\pip install akshare pandas"
}

$py = Resolve-Python -explicit $Python
Log "python: $py"

# --- weekend skip ---
$dow = (Get-Date).DayOfWeek
if (-not $Force -and ($dow -eq 'Saturday' -or $dow -eq 'Sunday')) {
  Log "skip: weekend ($dow). Pass -Force to override."
  exit 0
}

# --- git pull with retry ---
function Invoke-WithRetry {
  param([scriptblock]$Action, [string]$Label, [int]$Attempts = 4)
  for ($i = 1; $i -le $Attempts; $i++) {
    & $Action
    if ($LASTEXITCODE -eq 0) { return $true }
    $wait = 2 * $i
    Log "$Label failed (exit=$LASTEXITCODE), retry $i/$Attempts after ${wait}s..."
    Start-Sleep -Seconds $wait
  }
  return $false
}

$dirtyTracked = @(git status --porcelain --untracked-files=no)
if ($dirtyTracked.Count -gt 0) {
  Log "abort: tracked working tree has uncommitted changes. Commit/stash before daily refresh."
  $dirtyTracked | ForEach-Object { Log "  $_" }
  exit 2
}

Log "git pull --rebase origin $Branch ..."
$pullOk = Invoke-WithRetry -Label 'pull' -Action { git pull --rebase origin $Branch 2>&1 | ForEach-Object { Add-Content -Path $logFile -Value "  $_" -Encoding UTF8; $_ } | Out-Null }
if (-not $pullOk) {
  Log "abort: git pull failed after retries. Not fetching or committing on stale code."
  Log "If you only need to grab data manually, run scripts\fetch_all.ps1 -Refresh directly."
  exit 2
}

# --- fetch all ---
Log "running fetch_all.ps1 -Refresh ..."
# Use hashtable splat so the [switch] -Refresh parameter binds correctly.
# Array splat `@('-Refresh')` would bind '-Refresh' as a positional string
# (to $Symbols), leaving $Refresh = $false and stock.py reading today's cache.
$fetchArgs = @{ Refresh = $true }
if ($py) { $fetchArgs.Python = $py }
& (Join-Path $PSScriptRoot 'fetch_all.ps1') @fetchArgs
$fetchExit = $LASTEXITCODE
Log "fetch_all exit=$fetchExit"
if ($fetchExit -ne 0) {
  Log "abort: fetch_all failed. Not committing partial data."
  exit 5
}

# --- commit changed data ---
$dateTag = Get-Date -Format 'yyyyMMdd'
$dataDir = "data\$dateTag"

# --- post-fetch enrichments (idempotent, additive) ---
# MUST run BEFORE validation: enrichers fill basic_info (historical/hardcoded)
# and other fields whose absence the validator would otherwise flag. Running
# validate first would abort on exactly the gaps enrichment is designed to
# close. Enricher failures are non-fatal — we still proceed to validate+commit.
$enrichScripts = @(
  'scripts\enrich_basic_info.py',
  'scripts\enrich_margin_net.py',
  'scripts\enrich_sector_flow.py',
  'scripts\enrich_valuation.py',
  'scripts\enrich_streak.py',
  'scripts\enrich_divergence.py',
  'scripts\enrich_alpha.py',
  'scripts\enrich_score.py'
)
foreach ($script in $enrichScripts) {
  $fullPath = Join-Path $root $script
  if (-not (Test-Path $fullPath)) {
    Log "WARN: enricher missing: $script (skipped)"
    continue
  }
  Log "enrich: $script --date $dateTag"
  & $py $fullPath --date $dateTag 2>&1 | ForEach-Object { Log "  $_" }
  if ($LASTEXITCODE -ne 0) {
    Log "  WARN: enricher exit=$LASTEXITCODE (non-fatal, continuing)"
  }
}

# --- validate (after enrichment, so basic_info gaps are already filled) ---
# Pass --expected-count so the validator fails when fetch was partial. The
# count is parsed dynamically from fetch_all.ps1's $default array so it stays
# in sync as the watchlist grows.
$expectedCount = 0
$fetchScript = Join-Path $root 'scripts\fetch_all.ps1'
if (Test-Path $fetchScript) {
  try {
    $content = Get-Content $fetchScript -Raw
    if ($content -match '(?s)\$default\s*=\s*@\((.*?)\)') {
      $expectedCount = ([regex]::Matches($matches[1], "'\d{6}'")).Count
    }
  } catch { $expectedCount = 0 }
}

$validator = Join-Path $root '.claude\skills\china-stock-analysis\scripts\validate_data.py'
if (Test-Path $validator) {
  Log "validating $dataDir (expected-count=$expectedCount) ..."
  $validateArgs = @($validator, '--data-dir', $dataDir)
  if ($expectedCount -gt 0) { $validateArgs += @('--expected-count', $expectedCount) }
  & $py @validateArgs 2>&1 | ForEach-Object { Log "  $_" }
  if ($LASTEXITCODE -ne 0) {
    Log "abort: data validation failed (critical). Not committing bad snapshots."
    exit 6
  }
} else {
  Log "WARN: data validator missing: $validator"
}

# data/ is in .gitignore, so `git status --porcelain` won't list changes there
# unless we stage with -f first. Stage, then inspect the index.
git add -f $dataDir 2>&1 | Out-Null
$staged = @(git diff --cached --name-only -- $dataDir)
if ($staged.Count -eq 0) {
  Log "no data changes in $dataDir — nothing to commit"
  Log "=== daily_refresh done (no-op) ==="
  exit 0
}

Log "$($staged.Count) file(s) staged, committing..."
$msg = "Daily data refresh + enrich $dateTag"
git commit -m $msg 2>&1 | ForEach-Object { Log "  $_" }
if ($LASTEXITCODE -ne 0) {
  Log "commit failed (exit=$LASTEXITCODE). Aborting."
  exit 3
}

if ($NoPush) {
  Log "skipped push (-NoPush). Local commit created."
  Log "=== daily_refresh done ==="
  exit 0
}

Log "pushing to origin/$Branch ..."
$pushOk = Invoke-WithRetry -Label 'push' -Action { git push -u origin $Branch 2>&1 | ForEach-Object { Add-Content -Path $logFile -Value "  $_" -Encoding UTF8; $_ } | Out-Null }
if (-not $pushOk) {
  Log "push failed after retries. Local commit kept; retry manually later with: git push"
  exit 4
}

Log "=== daily_refresh done ==="
exit 0
