# ==============================================================================
# Remove Windows Scheduled Task for Flight Finder Daily Fare Scan
# ==============================================================================
$TaskName = "FlightFinder Daily Fare Scan"

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Successfully removed scheduled task: '$TaskName'" -ForegroundColor Green
} else {
    Write-Host "Scheduled task '$TaskName' is not currently registered." -ForegroundColor Yellow
}
