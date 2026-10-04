@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Please run setup-local.cmd first to install Flight Finder.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" local_app.py --open
if errorlevel 1 pause
