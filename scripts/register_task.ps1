# Register / re-register the ZzkkDailyRefresh scheduled task with all the
# settings needed for unattended (away-from-home) operation:
#   - wake the computer from sleep at run time
#   - catch up if missed (e.g. machine was off)
#   - auto-retry 3 times on failure, 15 min apart
#   - work on battery (laptops)
#   - 2 hour execution limit (covers 13 stocks even on slow networks)
#
# Re-run safe: unregisters and recreates.
#
# Usage:
#   pwsh scripts\register_task.ps1                              # default 18:00, no power config
#   pwsh scripts\register_task.ps1 -ConfigurePower              # also disable sleep/hibernate
#   pwsh scripts\register_task.ps1 -RunTime 17:30               # different daily time
#   pwsh scripts\register_task.ps1 -ExecutionTimeLimitHours 3   # raise time limit

[CmdletBinding()]
param(
  [string]$TaskName = "ZzkkDailyRefresh",
  [string]$RunTime  = "18:00",
  [int]$ExecutionTimeLimitHours = 2,
  [switch]$ConfigurePower
)

$ErrorActionPreference = 'Stop'
$repo = (Get-Item (Split-Path -Parent $PSScriptRoot)).FullName

Write-Host ""
Write-Host "Registering scheduled task '$TaskName' for repo: $repo" -ForegroundColor Cyan

# Prefer PowerShell 7+ (pwsh) if installed, fall back to Windows PowerShell 5.1
$pshCmd = Get-Command pwsh -ErrorAction SilentlyContinue
if (-not $pshCmd) { $pshCmd = Get-Command powershell }
$shell = $pshCmd.Source
Write-Host "  shell:    $shell"
Write-Host "  schedule: daily at $RunTime"
Write-Host "  timeout:  $ExecutionTimeLimitHours hour(s)"

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

$action = New-ScheduledTaskAction `
  -Execute $shell `
  -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$repo\scripts\daily_refresh.ps1`"" `
  -WorkingDirectory $repo

$trigger = New-ScheduledTaskTrigger -Daily -At $RunTime

$settings = New-ScheduledTaskSettingsSet `
  -WakeToRun `
  -StartWhenAvailable `
  -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 15) `
  -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit (New-TimeSpan -Hours $ExecutionTimeLimitHours)

$principal = New-ScheduledTaskPrincipal `
  -UserId $env:USERNAME `
  -LogonType Interactive `
  -RunLevel Limited

Register-ScheduledTask `
  -TaskName $TaskName `
  -Action $action `
  -Trigger $trigger `
  -Settings $settings `
  -Principal $principal `
  -Description "Daily A-share data refresh — 13 stocks + market" `
  | Out-Null

$info = Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo
Write-Host ""
Write-Host "  [OK] task registered" -ForegroundColor Green
Write-Host "  next run: $($info.NextRunTime)"
Write-Host "  flags:    WakeToRun, StartWhenAvailable, AllowStartIfOnBatteries"
Write-Host "  retry:    3x on failure, 15 min apart"

if ($ConfigurePower) {
  Write-Host ""
  Write-Host "Configuring power settings (no sleep, no hibernate, allow timer wake)..." -ForegroundColor Cyan
  powercfg /change standby-timeout-ac 0 | Out-Null
  powercfg /change standby-timeout-dc 0 | Out-Null
  powercfg /change hibernate-timeout-ac 0 | Out-Null
  powercfg /change hibernate-timeout-dc 0 | Out-Null
  powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP RTCWAKE 1 2>$null
  powercfg /setdcvalueindex SCHEME_CURRENT SUB_SLEEP RTCWAKE 1 2>$null
  powercfg /setactive SCHEME_CURRENT | Out-Null
  Write-Host "  [OK] sleep/hibernate disabled, RTC wake enabled" -ForegroundColor Green
}

Write-Host ""
Write-Host "Test now:" -ForegroundColor Yellow
Write-Host "  schtasks /Run /TN `"$TaskName`""
Write-Host ""
Write-Host "After it finishes, check:"
Write-Host "  Get-Content logs\daily_refresh_`$((Get-Date -Format yyyyMMdd)).log -Tail 30"
Write-Host ""
