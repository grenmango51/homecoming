# Skyscanner Daily Fare Scanner Runner (PowerShell)
# Usage: powershell -ExecutionPolicy Bypass -File .\run_daily_skyscanner.ps1

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$VenvPython = Join-Path $ScriptDir ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Error "Virtual environment not found at $VenvPython. Please run: python -m venv .venv"
    exit 1
}

$LogsDir = Join-Path $ScriptDir "flight_results_skyscanner\logs"
if (-not (Test-Path $LogsDir)) {
    New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null
}

$Today = (Get-Date).ToString("yyyy-MM-dd")
$LogFile = Join-Path $LogsDir "run_skyscanner_$Today.log"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " Starting Skyscanner Daily Fare Scan ($Today)" -ForegroundColor Cyan
Write-Host " Log file: $LogFile" -ForegroundColor DarkGray
Write-Host "==========================================================" -ForegroundColor Cyan

# Run find_flights_skyscanner.py with --skip-existing and config delay
& $VenvPython find_flights_skyscanner.py --skip-existing @args *>&1 | Tee-Object -FilePath $LogFile

$ScanExitCode = $LASTEXITCODE
if ($ScanExitCode -eq 0) {
    Write-Host "`nSkyscanner scan completed successfully!" -ForegroundColor Green
    
    $CsvReport = Join-Path $ScriptDir "flight_results_skyscanner\daily_fare_report_skyscanner_$Today.csv"
    if (Test-Path $CsvReport) {
        Write-Host "`nTop 10 Cheapest Flights Found on Skyscanner Today:" -ForegroundColor Yellow
        $Rows = Import-Csv $CsvReport | Where-Object { $_.lowest_observed_price_eur -ne "" } | Sort-Object { [double]$_.lowest_observed_price_eur }
        $Rows | Select-Object -First 10 departure_date, return_date, stay_nights, lowest_observed_price_eur | Format-Table -AutoSize
    }
} else {
    Write-Host "`nSkyscanner scan encountered an issue (Exit Code: $ScanExitCode). Check log: $LogFile" -ForegroundColor Red
}

exit $ScanExitCode

