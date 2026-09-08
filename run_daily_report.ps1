$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$scanner = Join-Path $projectRoot 'find_flights.py'

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment not found: $python"
}

# Opens the dedicated, visible Google Flights browser profile and writes a
# timestamped 25-row CSV and JSON report under flight_results/.
& $python $scanner @args
exit $LASTEXITCODE
