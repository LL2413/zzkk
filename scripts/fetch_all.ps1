# Batch snapshot fetcher for the china-stock-analysis skill (Windows / PowerShell).
#
# Usage:
#   pwsh scripts\fetch_all.ps1                       # default watchlist
#   pwsh scripts\fetch_all.ps1 002281 000988         # custom symbols
#   pwsh scripts\fetch_all.ps1 -Refresh              # bypass daily cache
#   pwsh scripts\fetch_all.ps1 -Python "C:\path\to\python.exe"
#
# Output layout:
#   data\YYYYMMDD\
#     market.json
#     <symbol>_snapshot.json
#     _manifest.txt
#
# Mirrors scripts/fetch_all.sh for non-WSL Windows environments.

[CmdletBinding()]
param(
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$Symbols = @(),
  [switch]$Refresh,
  [string]$Python = $null,
  [string]$WatchlistFile = $null
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# --- pick python ---
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

# --- resolve watchlist ---
$default = @('002281','000988','688008','603986','688728','688332')
if ($Symbols.Count -gt 0) {
  $watchlist = $Symbols
} elseif ($WatchlistFile -and (Test-Path $WatchlistFile)) {
  $watchlist = Get-Content $WatchlistFile |
    Where-Object { $_ -match '^\s*(\d{6})' } |
    ForEach-Object { $Matches[1] }
} else {
  $watchlist = $default
}

$forceFlag = if ($Refresh) { @('--force') } else { @() }

$dateTag = Get-Date -Format 'yyyyMMdd'
$outDir  = Join-Path $root "data\$dateTag"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$manifest = Join-Path $outDir '_manifest.txt'
Set-Content -Path $manifest -Value '' -Encoding UTF8

function Log([string]$msg) {
  $line = '[{0}] {1}' -f (Get-Date -Format 'HH:mm:ss'), $msg
  Write-Host $line
  Add-Content -Path $manifest -Value $line -Encoding UTF8
}

Log "python:  $py"
Log "out_dir: $outDir"
Log "refresh: $([bool]$Refresh)"
Log "symbols: $($watchlist -join ' ')"
Log ""

# --- market overview ---
Log "fetch market ..."
$marketFile = Join-Path $outDir 'market.json'
& $py scripts\stock.py market @forceFlag --json > $marketFile 2>> $manifest
if ($LASTEXITCODE -eq 0 -and (Test-Path $marketFile)) {
  $bytes = (Get-Item $marketFile).Length
  Log "  ok  $bytes bytes -> $marketFile"
} else {
  Log "  FAIL (exit=$LASTEXITCODE) see manifest"
}

# --- per-symbol snapshots ---
$ok = 0; $fail = 0
foreach ($sym in $watchlist) {
  Log "fetch $sym ..."
  $outFile = Join-Path $outDir "${sym}_snapshot.json"
  & $py scripts\stock.py snapshot $sym @forceFlag --json > $outFile 2>> $manifest
  if ($LASTEXITCODE -eq 0 -and (Test-Path $outFile) -and (Get-Item $outFile).Length -gt 1024) {
    $bytes = (Get-Item $outFile).Length
    Log "  ok  $bytes bytes -> $outFile"
    $ok++
  } else {
    Log "  FAIL (exit=$LASTEXITCODE) see manifest"
    $fail++
  }
}

Log ""
Log "done: ok=$ok fail=$fail total=$($watchlist.Count)"
Log "inspect: Get-ChildItem $outDir"

if ($fail -gt 0) { exit 1 } else { exit 0 }
