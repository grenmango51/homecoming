@echo off
setlocal
cd /d "%~dp0"

set VENV_PYTHON=%~dp0.venv\Scripts\python.exe
if not exist "%VENV_PYTHON%" (
    echo Error: Virtual environment python not found at %VENV_PYTHON%
    pause
    exit /b 1
)

echo ========================================================
echo Starting Google Flights Daily Fare Scan
echo ========================================================

"%VENV_PYTHON%" find_flights.py --skip-existing --delay-seconds 3

if %ERRORLEVEL% equ 0 (
    echo.
    echo Scan completed successfully!
    echo Check flight_results folder for daily_fare_report.csv and json.
) else (
    echo.
    echo Scan stopped with error code %ERRORLEVEL%.
)

echo.
pause
