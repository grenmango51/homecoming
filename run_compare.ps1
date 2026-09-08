# Flight Comparison Runner: Google Flights vs. Skyscanner (PowerShell)
# Usage: powershell -ExecutionPolicy Bypass -File .\run_compare.ps1

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$VenvPython = Join-Path $ScriptDir ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Error "Virtual environment not found at $VenvPython. Please run: python -m venv .venv"
    exit 1
}

$Script = Join-Path $ScriptDir "compare_flights.py"
& $VenvPython $Script @args
exit $LASTEXITCODE

