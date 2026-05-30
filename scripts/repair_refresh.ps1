# Repair a partial current-day refresh without re-fetching healthy snapshots.
#
# Examples:
#   powershell -ExecutionPolicy Bypass -File scripts\repair_refresh.ps1 -DryRun
#   powershell -ExecutionPolicy Bypass -File scripts\repair_refresh.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\repair_refresh.ps1 -Force
#   powershell -ExecutionPolicy Bypass -File scripts\repair_refresh.ps1 688981 601138
#   powershell -ExecutionPolicy Bypass -File scripts\repair_refresh.ps1 -NoFetch -NoPull -NoCommit

[CmdletBinding(PositionalBinding=$false)]
param(
  [Parameter(Position=0, ValueFromRemainingArguments=$true)]
  [string[]]$Symbols = @(),
  [string]$DateTag = (Get-Date -Format 'yyyyMMdd'),
  [switch]$RefreshAll,
  [switch]$Force,
  [switch]$NoFetch,
  [switch]$NoPull,
  [switch]$NoCommit,
  [switch]$NoPush,
  [switch]$DryRun,
  [switch]$RequireStrongFundFlow,
  [string]$Branch = 'claude/stock-market-analysis-skill-9p7lc',
  [string]$Python = $null,
  [int]$CommandTimeoutSeconds = 600
)

$ErrorActionPreference = 'Continue'
. (Join-Path $PSScriptRoot 'workflow_common.ps1')
Set-Location $workflowRepoRoot

try {
  Assert-WorkflowDateTag -DateTag $DateTag
} catch {
  Write-Error $_.Exception.Message
  exit 2
}

$logDir = Join-Path $workflowRepoRoot 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir ("repair_refresh_{0}_{1}.log" -f $DateTag, (Get-Date -Format 'HHmmss'))
function Log([string]$Message) {
  $line = '[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
  Write-Host $line
  Add-Content -Path $logFile -Value $line -Encoding UTF8
}

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

Log "=== repair_refresh start ==="
Log "cwd: $workflowRepoRoot"

try {
  $py = Resolve-WorkflowPython -Explicit $Python
  $watchlist = @(Get-WorkflowWatchlistSymbols)
} catch {
  Log "abort: $($_.Exception.Message)"
  exit 2
}
Log "python: $py"

$today = Get-Date -Format 'yyyyMMdd'
if (-not $NoFetch -and $DateTag -ne $today) {
  Log "abort: historical refetch is unsafe. Use today's DateTag ($today), or pass -NoFetch to finalize existing historical data."
  exit 2
}

$unknown = @($Symbols | Where-Object { $_ -notmatch '^\d{6}$' -or $_ -notin $watchlist })
if ($unknown.Count -gt 0) {
  Log "abort: symbols are not in the default watchlist: $($unknown -join ' ')"
  exit 2
}

$dataDir = Join-Path $workflowRepoRoot "data\$DateTag"
$marketPath = Join-Path $dataDir 'market.json'
$marketUsable = Test-WorkflowJsonFile -Path $marketPath
$unusable = @(Get-WorkflowUnusableSnapshotSymbols -DataDir $dataDir -Symbols $watchlist)

if ($RefreshAll) {
  $targets = $watchlist
} elseif ($Symbols.Count -gt 0) {
  $targets = @($Symbols | Select-Object -Unique)
} else {
  $targets = $unusable
}

Log "date: $DateTag"
Log "market.json: $(if ($marketUsable) { 'ok' } else { 'missing or unreadable' })"
Log "snapshots unusable: $($unusable.Count)/$($watchlist.Count)"
if ($unusable.Count -gt 0) { Log "unusable symbols: $($unusable -join ' ')" }
Log "repair targets: $($targets.Count)"
if ($targets.Count -gt 0) { Log "target symbols: $($targets -join ' ')" }

if ($DryRun) {
  Log "dry-run: no files changed"
  exit 0
}

$dow = (Get-Date).DayOfWeek
if (-not $NoFetch -and -not $Force -and ($dow -eq 'Saturday' -or $dow -eq 'Sunday')) {
  Log "abort: weekend ($dow). Pass -Force only if you intentionally want a weekend refresh."
  exit 2
}

$dirtyTracked = @(git status --porcelain --untracked-files=no)
if ($dirtyTracked.Count -gt 0 -and (-not $NoPull -or -not $NoCommit)) {
  Log "abort: tracked working tree has uncommitted changes. Commit/stash first, or use -NoPull -NoCommit for a local-only finalize."
  $dirtyTracked | ForEach-Object { Log "  $_" }
  exit 2
}

if (-not $NoPull) {
  Log "git pull --rebase origin $Branch ..."
  $pullOk = Invoke-WithRetry -Label 'pull' -Action {
    git pull --rebase origin $Branch 2>&1 |
      ForEach-Object { Add-Content -Path $logFile -Value "  $_" -Encoding UTF8; $_ } |
      Out-Null
  }
  if (-not $pullOk) {
    Log "abort: git pull failed after retries. Pass -NoPull only if you intentionally want local code."
    exit 2
  }
}

if (-not $NoFetch) {
  $fetchScript = Join-Path $PSScriptRoot 'fetch_all.ps1'
  $fetchArgs = @{
    Refresh = $true
    Python = $py
    CommandTimeoutSeconds = $CommandTimeoutSeconds
  }
  if ($targets.Count -gt 0) {
    Log "refetching $($targets.Count) target snapshot(s) ..."
    & $fetchScript @fetchArgs -Symbols $targets
  } elseif (-not $marketUsable) {
    Log "snapshots are healthy; refetching market.json only ..."
    & $fetchScript @fetchArgs -MarketOnly
  } else {
    Log "no refetch needed; proceeding to finalize"
  }
  if ($LASTEXITCODE -ne 0) {
    Log "abort: targeted fetch failed (exit=$LASTEXITCODE). Inspect data\$DateTag\_manifest.txt"
    exit 5
  }
}

Log "running deterministic finalize ..."
$finalizeArgs = @{
  DateTag = $DateTag
  Python = $py
}
if ($RequireStrongFundFlow) { $finalizeArgs.RequireStrongFundFlow = $true }
& (Join-Path $PSScriptRoot 'finalize_refresh.ps1') @finalizeArgs 2>&1 | ForEach-Object { Log "  $_" }
$finalizeExit = $LASTEXITCODE
if ($finalizeExit -ne 0) {
  Log "abort: finalize failed (exit=$finalizeExit)"
  exit 6
}

if ($NoCommit) {
  Log "skipped commit (-NoCommit)"
  Log "=== repair_refresh done ==="
  exit 0
}

$reportRel = "reports\watchlist_{0}.md" -f $DateTag
$reportPath = Join-Path $workflowRepoRoot $reportRel
git add -f "data\$DateTag" 2>&1 | Out-Null
if (Test-Path $reportPath) { git add $reportRel 2>&1 | Out-Null }
$staged = @(git diff --cached --name-only -- "data\$DateTag" $reportRel)
if ($staged.Count -eq 0) {
  Log "no repaired data changes to commit"
  Log "=== repair_refresh done (no-op) ==="
  exit 0
}

Log "$($staged.Count) file(s) staged, committing ..."
git commit -m "Repair data refresh + analysis $DateTag" 2>&1 | ForEach-Object { Log "  $_" }
if ($LASTEXITCODE -ne 0) {
  Log "abort: commit failed (exit=$LASTEXITCODE)"
  exit 3
}

if ($NoPush) {
  Log "skipped push (-NoPush). Local commit created."
  Log "=== repair_refresh done ==="
  exit 0
}

Log "pushing to origin/$Branch ..."
$pushOk = Invoke-WithRetry -Label 'push' -Action {
  git push -u origin $Branch 2>&1 |
    ForEach-Object { Add-Content -Path $logFile -Value "  $_" -Encoding UTF8; $_ } |
    Out-Null
}
if (-not $pushOk) {
  Log "push failed after retries. Local commit kept; retry manually later with: git push"
  exit 4
}

Log "=== repair_refresh done ==="
exit 0
