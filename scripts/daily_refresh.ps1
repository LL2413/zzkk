# Daily auto-refresh for china-stock-analysis data.
# Suitable for Windows Task Scheduler.
#
# What it does:
#   1. cd to repo, git pull (retry 4x on transient network failures)
#   2. Skip weekends (A-share markets closed; data unchanged)
#   3. Run fetch_all.ps1 -Refresh for all watchlist symbols
#   4. Run deterministic finalize_refresh.ps1:
#      enrich -> validate -> _signal_score post-flight -> report
#   5. Generate reports/watchlist_<today>.md from the validated snapshots
#   6. If any data/<today> or report file changed, commit and push to the tracked branch
#   7. Log everything to logs/daily_refresh_<date>.log (under .gitignore)
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
  [string]$Python = $null,
  [int]$CommandTimeoutSeconds = 600
)

$ErrorActionPreference = 'Continue'  # don't abort on single-source network flakes
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
. (Join-Path $PSScriptRoot 'workflow_common.ps1')

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

$py = Resolve-WorkflowPython -Explicit $Python
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
$fetchArgs = @{ Refresh = $true; CommandTimeoutSeconds = $CommandTimeoutSeconds }
if ($py) { $fetchArgs.Python = $py }
& (Join-Path $PSScriptRoot 'fetch_all.ps1') @fetchArgs
$fetchExit = $LASTEXITCODE
Log "fetch_all exit=$fetchExit"
if ($fetchExit -ne 0) {
  Log "abort: fetch_all failed. Not committing partial data."
  exit 5
}

# --- deterministic finalize ---
$dateTag = Get-Date -Format 'yyyyMMdd'
$dataDir = "data\$dateTag"
Log "running finalize_refresh.ps1 ..."
& (Join-Path $PSScriptRoot 'finalize_refresh.ps1') -DateTag $dateTag -Python $py 2>&1 |
  ForEach-Object { Log "  $_" }
$finalizeExit = $LASTEXITCODE
if ($finalizeExit -ne 0) {
  Log "abort: finalize_refresh failed (exit=$finalizeExit). Not committing incomplete data."
  exit 6
}

# Stage the target data directory explicitly, then inspect only this run's
# index entries so scratch files and partial historical dirs stay untouched.
$reportRel = "reports\watchlist_{0}.md" -f $dateTag
$reportPath = Join-Path $root $reportRel
git add -f $dataDir 2>&1 | Out-Null
if (Test-Path $reportPath) {
  git add $reportRel 2>&1 | Out-Null
}
$staged = @(git diff --cached --name-only -- $dataDir $reportRel)
if ($staged.Count -eq 0) {
  Log "no data changes in $dataDir — nothing to commit"
  Log "=== daily_refresh done (no-op) ==="
  exit 0
}

Log "$($staged.Count) file(s) staged, committing..."
$msg = "Daily data refresh + analysis $dateTag"
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
