# Helper to launch Chrome with the Skyscanner persistent profile for one-time verification
$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProfileDir = Join-Path $ScriptDir ".skyscanner-profile"
$Chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"

if (-not (Test-Path $Chrome)) {
    Write-Error "Chrome executable not found at $Chrome"
    exit 1
}

Write-Host "Opening Chrome with Skyscanner scraper profile..." -ForegroundColor Cyan
Write-Host "Solve any verification challenge once, browse for a minute, then close the browser." -ForegroundColor Yellow
Start-Process $Chrome -ArgumentList "--user-data-dir=`"$ProfileDir`"", "https://www.skyscanner.fi"
