@echo off
cd /d "%~dp0"
python -m venv .venv
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m patchright install chromium
if errorlevel 1 goto failed
echo Setup complete. Double-click start-local.cmd to search flights.
pause
exit /b 0
:failed
echo Setup failed. Install Python 3.11 or newer, then retry. See docs\WEB_UI.md.
pause
exit /b 1
