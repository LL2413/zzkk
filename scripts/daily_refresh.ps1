# Daily auto-refresh for china-stock-analysis data.
# Suitable for Windows Task Scheduler.
#
# What it does:
#   1. cd to repo, git pull (retry 4x on transient network failures)
#   2. Skip weekends (A-share markets closed; data unchanged)
#   3. Run fetch_all.ps1 -Refresh for all watchlist symbols
#   4. If any data/<today> file changed, commit and push to the tracked branch
#   5. Log everything to logs/daily_refresh_<date>.log (under .gitignore)
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

Log "git pull origin $Branch ..."
$pullOk = Invoke-WithRetry -Label 'pull' -Action { git pull origin $Branch 2>&1 | ForEach-Object { Add-Content -Path $logFile -Value "  $_" -Encoding UTF8; $_ } | Out-Null }
if (-not $pullOk) {
  Log "FATAL: git pull failed after retries. Aborting."
  exit 2
}

# --- fetch all ---
Log "running fetch_all.ps1 -Refresh ..."
$fetchArgs = @('-Refresh')
if ($Python) { $fetchArgs += @('-Python', $Python) }
& (Join-Path $PSScriptRoot 'fetch_all.ps1') @fetchArgs
$fetchExit = $LASTEXITCODE
Log "fetch_all exit=$fetchExit"

# --- commit changed data ---
$dateTag = Get-Date -Format 'yyyyMMdd'
$dataDir = "data\$dateTag"

$status = git status --porcelain -- $dataDir 2>&1
if (-not $status) {
  Log "no data changes — nothing to commit"
  Log "=== daily_refresh done (no-op) ==="
  exit 0
}

Log "data changes detected, committing..."
git add -f $dataDir 2>&1 | Out-Null
$msg = "Daily data refresh $dateTag"
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
