# ==============================================================================
# Setup Windows Scheduled Task for Flight Finder Daily Fare Scan
# Runs twice daily: at 9:00 AM and 9:00 PM (or on computer wake if missed).
# Completely hidden execution (no console window flashing).
# ==============================================================================
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$TaskName = "FlightFinder Daily Fare Scan"
$SilentVbs = Join-Path $ScriptDir "run_daily_silent.vbs"

if (-not (Test-Path $SilentVbs)) {
    Write-Error "Launcher script not found: $SilentVbs"
    exit 1
}

$Action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$SilentVbs`"" -WorkingDirectory $ProjectRoot

# Two daily triggers: 9:00 AM and 9:00 PM
$TriggerMorning = New-ScheduledTaskTrigger -Daily -At 9:00AM
$TriggerEvening = New-ScheduledTaskTrigger -Daily -At 9:00PM
$Triggers = @($TriggerMorning, $TriggerEvening)

$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -MultipleInstances IgnoreNew

$Principal = New-ScheduledTaskPrincipal -UserId "$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName `
    -Action $Action `
    -Trigger $Triggers `
    -Settings $Settings `
    -Principal $Principal `
    -Description "Automated flight fare scraper for Helsinki-Hanoi. Runs silently twice daily: at 9:00 AM and 9:00 PM." `
    -Force | Out-Null

Write-Host "Successfully registered scheduled task: '$TaskName'" -ForegroundColor Green
Write-Host "Schedule: Twice daily at 9:00 AM and 9:00 PM" -ForegroundColor Cyan
Write-Host "Missed Run Catch-up: Enabled (StartWhenAvailable = True)" -ForegroundColor Cyan
Write-Host "Mode: 100% Silent via wscript.exe (No console window)" -ForegroundColor Cyan
