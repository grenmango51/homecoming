# Flight Finder Unified Parallel Runner (PowerShell)
# Usage: powershell -ExecutionPolicy Bypass -File .\run_all.ps1

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$VenvPython = Join-Path $ScriptDir ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Error "Virtual environment not found at $VenvPython. Please run: python -m venv .venv"
    exit 1
}

$RunAllScript = Join-Path $ScriptDir "run_all.py"
& $VenvPython $RunAllScript @args
exit $LASTEXITCODE
