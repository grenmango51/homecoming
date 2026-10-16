# ==============================================================================
# Flight Finder - Daily Scheduled Runner
# ==============================================================================
$ErrorActionPreference = "Continue"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Error "Virtual environment not found at $VenvPython"
    exit 1
}

$Today = (Get-Date).ToString("yyyy-MM-dd")
$LogsDir = Join-Path $ProjectRoot "flight_results\logs"
if (-not (Test-Path $LogsDir)) {
    New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null
}
$ScheduledLog = Join-Path $LogsDir "scheduled_run_$Today.log"

$msgStart = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] [Scheduled] Starting fare scan..."
Add-Content -Path $ScheduledLog -Value $msgStart -Encoding utf8

$sw = [System.Diagnostics.Stopwatch]::StartNew()
& $VenvPython run_all.py --trip HEL_HAN.toml *>> $ScheduledLog
$exitCode = $LASTEXITCODE
$sw.Stop()

$msgEnd = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] [Scheduled] Scan finished in $($sw.Elapsed.ToString('hh\:mm\:ss')) (exit code: $exitCode)."
Add-Content -Path $ScheduledLog -Value $msgEnd -Encoding utf8
exit $exitCode
