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
  [switch]$Refresh,
  [string]$Python = $null,
  [string]$WatchlistFile = $null
)

# Use $args (auto-variable) instead of [Parameter(ValueFromRemainingArguments)]
# because the latter combined with [string[]] silently drops the first 2
# positional arguments under powershell.exe -File invocation in PS 5.1.
# Repro: `pwsh fetch_all.ps1 002281 000988 688008` would only fetch 688008.
$Symbols = @($args | Where-Object { $_ -and "$_".Length -gt 0 })

# PowerShell 5.1 treats ANY stderr output from native commands as a terminating
# error under 'Stop'. akshare / urllib3 routinely emit FutureWarning etc. to
# stderr; we want to capture those in the manifest, not abort the batch.
$ErrorActionPreference = 'Continue'
$ProgressPreference    = 'SilentlyContinue'
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
$default = @(
  '002281',  # 光迅科技
  '000988',  # 华工科技
  '688008',  # 澜起科技
  '603986',  # 兆易创新
  '688728',  # 格科微
  '688332',  # 中科蓝讯
  '688046',  # 药康生物
  '688380',  # 中微半导
  '688123',  # 聚辰股份 (XD)
  '688550',  # 瑞联新材
  '688208',  # 道通科技
  '002475',  # 立讯精密
  '300458',  # 全志科技
  '601869',  # 长飞光纤
  '600522',  # 中天科技
  '600487',  # 亨通光电
  '300395',  # 菲利华
  '300408'   # 三环集团
)
if ($Symbols.Count -gt 0) {
  $watchlist = $Symbols
} elseif ($WatchlistFile -and (Test-Path $WatchlistFile)) {
  $watchlist = Get-Content $WatchlistFile |
    Where-Object { $_ -match '^\s*(\d{6})' } |
    ForEach-Object { $Matches[1] }
} else {
  $watchlist = $default
}

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

function Invoke-StockJson {
  param(
    [string[]]$CommandArgs,
    [string]$OutFile
  )

  # PowerShell 5.1 writes native-command redirection (`>`) as UTF-16.
  # Capture stdout and write it explicitly as UTF-8 so JSON stays portable.
  $stdout = & $py @CommandArgs 2>> $manifest
  $exitCode = $LASTEXITCODE
  if ($exitCode -eq 0) {
    $stdout | Set-Content -Path $OutFile -Encoding UTF8
  }
  return $exitCode
}

Log "python:  $py"
Log "out_dir: $outDir"
Log "refresh: $([bool]$Refresh)"
Log "symbols: $($watchlist -join ' ')"
Log ""

# --- market overview ---
Log "fetch market ..."
$marketFile = Join-Path $outDir 'market.json'
if ($Refresh) {
  $marketExit = Invoke-StockJson -CommandArgs @('scripts\stock.py', 'market', '--force', '--json') -OutFile $marketFile
} else {
  $marketExit = Invoke-StockJson -CommandArgs @('scripts\stock.py', 'market', '--json') -OutFile $marketFile
}
if ($marketExit -eq 0 -and (Test-Path $marketFile)) {
  $bytes = (Get-Item $marketFile).Length
  Log "  ok  $bytes bytes -> $marketFile"
} else {
  Log "  FAIL (exit=$marketExit) see manifest"
  Remove-Item $marketFile -Force -ErrorAction SilentlyContinue
}

# --- per-symbol snapshots ---
$ok = 0; $fail = 0
foreach ($sym in $watchlist) {
  Log "fetch $sym ..."
  $outFile = Join-Path $outDir "${sym}_snapshot.json"
  # Inline the conditional to avoid PowerShell 5.1's splat-on-string bug:
  # `$forceFlag = @('--force')` then `@forceFlag` was being splat as 7 chars.
  if ($Refresh) {
    $stockExit = Invoke-StockJson -CommandArgs @('scripts\stock.py', 'snapshot', $sym, '--force', '--json') -OutFile $outFile
  } else {
    $stockExit = Invoke-StockJson -CommandArgs @('scripts\stock.py', 'snapshot', $sym, '--json') -OutFile $outFile
  }
  if ($stockExit -eq 0 -and (Test-Path $outFile) -and (Get-Item $outFile).Length -gt 1024) {
    $bytes = (Get-Item $outFile).Length
    Log "  ok  $bytes bytes -> $outFile"
    $ok++
  } else {
    Log "  FAIL (exit=$stockExit) see manifest"
    # Delete truncated/empty output so next run doesn't trip on JSONDecodeError
    Remove-Item $outFile -Force -ErrorAction SilentlyContinue
    $fail++
  }
}

Log ""
Log "done: ok=$ok fail=$fail total=$($watchlist.Count)"
Log "inspect: Get-ChildItem $outDir"

if ($fail -gt 0) { exit 1 } else { exit 0 }
