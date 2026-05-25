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

# PositionalBinding=$false stops -Refresh / -Python / -WatchlistFile from
# silently consuming the first positional args. Symbols then collects every
# bare token via Position=0 + ValueFromRemainingArguments=$true.
# Works for both `.\fetch_all.ps1 002281 000988` (direct invocation) and
# `powershell.exe -File fetch_all.ps1 002281 000988` under PS 5.1 + 7.
[CmdletBinding(PositionalBinding=$false)]
param(
  [switch]$Refresh,
  [string]$Python = $null,
  [string]$WatchlistFile = $null,
  [int]$CommandTimeoutSeconds = 600,
  [Parameter(Position=0, ValueFromRemainingArguments=$true)]
  [string[]]$Symbols = @()
)
$Symbols = @($Symbols | Where-Object { $_ -and "$_".Length -gt 0 })

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
  '300408',  # 三环集团
  '603256',  # 宏和科技 (电子布/玻纤，半导体封装+PCB 上游)
  '603773',  # 沃格光电 (光电玻璃精加工)
  '000021',  # 深科技     (存储芯片封测/电子制造)
  '688627',  # 精智达     (半导体/显示测试设备)
  '688206',  # 概伦电子   (EDA 软件)
  '688521',  # 芯原股份   (芯片设计服务/IP)
  '688047',  # 龙芯中科   (国产 CPU)
  '600845',  # 宝信软件   (工业软件/IDC)
  '300499',  # 高澜股份   (电力电子温控/液冷)
  '002837',  # 英维克     (精密温控/数据中心液冷)
  '002156',  # 通富微电   (半导体封测)
  '600584',  # 长电科技   (半导体封测 JCET)
  '688981',  # 中芯国际   (晶圆代工)
  '688347',  # 华虹公司   (晶圆代工)
  '601138'   # 工业富联   (服务器/AI 算力硬件)
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

function Stop-ProcessTree([int]$ProcessId) {
  $children = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.ParentProcessId -eq $ProcessId })
  foreach ($child in $children) {
    Stop-ProcessTree -ProcessId ([int]$child.ProcessId)
  }
  Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

function Quote-ProcessArg([string]$arg) {
  if ($arg -notmatch '[\s"]') { return $arg }
  return '"' + ($arg -replace '\\(?=")', '\\' -replace '"', '\"') + '"'
}

function Invoke-StockJson {
  param(
    [string[]]$CommandArgs,
    [string]$OutFile
  )

  # PowerShell 5.1 can hang indefinitely when a native child blocks in an
  # akshare endpoint. Run each stock fetch as its own process and kill the
  # process tree after a bounded wait so one bad symbol cannot stall the batch.
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $py
  $psi.Arguments = (($CommandArgs | ForEach-Object { Quote-ProcessArg $_ }) -join ' ')
  $psi.UseShellExecute = $false
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $psi.CreateNoWindow = $true
  $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
  $psi.StandardErrorEncoding = [System.Text.Encoding]::UTF8

  $proc = New-Object System.Diagnostics.Process
  $proc.StartInfo = $psi
  try {
    [void]$proc.Start()
    $stdoutTask = $proc.StandardOutput.ReadToEndAsync()
    $stderrTask = $proc.StandardError.ReadToEndAsync()

    if (-not $proc.WaitForExit($CommandTimeoutSeconds * 1000)) {
      Log "  TIMEOUT after ${CommandTimeoutSeconds}s: $($CommandArgs -join ' ')"
      Stop-ProcessTree -ProcessId $proc.Id
      return 124
    }

    $proc.WaitForExit()
    $exitCode = $proc.ExitCode
    $stdout = $stdoutTask.Result
    $stderr = $stderrTask.Result
    if ($stderr) { Add-Content -Path $manifest -Value $stderr -Encoding UTF8 }
    if ($exitCode -eq 0) {
      [System.IO.File]::WriteAllText($OutFile, $stdout, [System.Text.Encoding]::UTF8)
    }
    return $exitCode
  } finally {
    if ($proc) { $proc.Dispose() }
  }
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
if ($marketExit -eq 0 -and (Test-Path $marketFile) -and (Get-Item $marketFile).Length -gt 1024) {
  $bytes = (Get-Item $marketFile).Length
  Log "  ok  $bytes bytes -> $marketFile"
} else {
  # Mirror the per-symbol guard: a sub-1KB market.json is a truncated/empty
  # write (seen as a 3-byte UTF-16 stub). Delete it so the validator doesn't
  # later abort on an unreadable-json critical error.
  $sz = if (Test-Path $marketFile) { (Get-Item $marketFile).Length } else { 0 }
  Log "  FAIL (exit=$marketExit, size=$sz bytes) see manifest"
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
