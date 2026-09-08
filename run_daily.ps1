# Google Flights Daily Fare Scanner Runner (PowerShell)
# Usage: powershell -ExecutionPolicy Bypass -File .\run_daily.ps1

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$VenvPython = Join-Path $ScriptDir ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Error "Virtual environment not found at $VenvPython. Please run: python -m venv .venv"
    exit 1
}

$LogsDir = Join-Path $ScriptDir "flight_results\logs"
if (-not (Test-Path $LogsDir)) {
    New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null
}

$Today = (Get-Date).ToString("yyyy-MM-dd")
$LogFile = Join-Path $LogsDir "run_$Today.log"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " Starting Google Flights Daily Fare Scan ($Today)" -ForegroundColor Cyan
Write-Host " Log file: $LogFile" -ForegroundColor DarkGray
Write-Host "==========================================================" -ForegroundColor Cyan

# Run find_flights.py with --skip-existing and 3s delay
& $VenvPython find_flights.py --skip-existing --delay-seconds 3 *>&1 | Tee-Object -FilePath $LogFile

$ScanExitCode = $LASTEXITCODE
if ($ScanExitCode -eq 0) {
    Write-Host "`nScan completed successfully!" -ForegroundColor Green
    
    $CsvReport = Join-Path $ScriptDir "flight_results\daily_fare_report_$Today.csv"
    if (Test-Path $CsvReport) {
        Write-Host "`nTop 10 Cheapest Flights Found Today:" -ForegroundColor Yellow
        $Rows = Import-Csv $CsvReport | Sort-Object { [double]$_.lowest_observed_price_eur }
        $Rows | Select-Object -First 10 departure_date, return_date, stay_nights, lowest_observed_price_eur | Format-Table -AutoSize
    }
} else {
    Write-Host "`nScan encountered an error or stopped (Exit Code: $ScanExitCode). Check log: $LogFile" -ForegroundColor Red
}

exit $ScanExitCode
