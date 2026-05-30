# Shared helpers for the packaged A-share refresh workflows.

$workflowScriptsRoot = $PSScriptRoot
$workflowRepoRoot = Split-Path -Parent $workflowScriptsRoot

function Assert-WorkflowDateTag {
  param([string]$DateTag)
  if ($DateTag -notmatch '^\d{8}$') {
    throw "DateTag must use YYYYMMDD format: $DateTag"
  }
}
function Resolve-WorkflowPython {
  param([string]$Explicit)
  if ($Explicit -and (Test-Path $Explicit)) { return $Explicit }
  $candidates = @(
    (Join-Path $workflowRepoRoot '.venv\Scripts\python.exe'),
    "$env:USERPROFILE\.venv\Scripts\python.exe",
    "$env:USERPROFILE\.venv\Scripts\python"
  )
  foreach ($path in $candidates) {
    if (Test-Path $path) { return $path }
  }
  $which = Get-Command python -ErrorAction SilentlyContinue
  if ($which) { return $which.Source }
  throw "No Python interpreter found. Create a venv: python -m venv .venv; .venv\Scripts\pip install akshare pandas"
}

function Get-WorkflowWatchlistSymbols {
  $fetchScript = Join-Path $workflowScriptsRoot 'fetch_all.ps1'
  if (-not (Test-Path $fetchScript)) {
    throw "Missing fetch script: $fetchScript"
  }
  $content = Get-Content $fetchScript -Raw
  if ($content -notmatch '(?s)\$default\s*=\s*@\((.*?)\)\s*if\s*\(') {
    throw "Could not parse default watchlist from: $fetchScript"
  }
  $symbols = @(
    [regex]::Matches($matches[1], "'(\d{6})'") |
      ForEach-Object { $_.Groups[1].Value } |
      Select-Object -Unique
  )
  if ($symbols.Count -eq 0) {
    throw "Default watchlist is empty in: $fetchScript"
  }
  return $symbols
}

function Get-WorkflowJsonObject {
  param([string]$Path)
  if (-not (Test-Path $Path)) { return $null }
  if ((Get-Item $Path).Length -le 0) { return $null }
  foreach ($encoding in @('UTF8', 'Unicode')) {
    try {
      return Get-Content -Path $Path -Raw -Encoding $encoding | ConvertFrom-Json
    } catch {}
  }
  return $null
}

function Test-WorkflowJsonFile {
  param(
    [string]$Path,
    [int]$MinimumBytes = 1024
  )
  if (-not (Test-Path $Path)) { return $false }
  if ((Get-Item $Path).Length -le $MinimumBytes) { return $false }
  return $null -ne (Get-WorkflowJsonObject -Path $Path)
}

function Get-WorkflowUnusableSnapshotSymbols {
  param(
    [string]$DataDir,
    [string[]]$Symbols
  )
  return @(
    $Symbols | Where-Object {
      $path = Join-Path $DataDir ("{0}_snapshot.json" -f $_)
      -not (Test-WorkflowJsonFile -Path $path)
    }
  )
}

function Get-WorkflowMissingSignalScoreSymbols {
  param([string]$DataDir)
  $missing = @()
  foreach ($snapshot in @(Get-ChildItem -Path $DataDir -Filter '*_snapshot.json' -File -ErrorAction SilentlyContinue)) {
    $data = Get-WorkflowJsonObject -Path $snapshot.FullName
    $hasScore = $data -and
      ($data.PSObject.Properties.Name -contains '_signal_score') -and
      ($null -ne $data._signal_score)
    if (-not $hasScore) {
      $missing += $snapshot.BaseName.Replace('_snapshot', '')
    }
  }
  return @($missing)
}
