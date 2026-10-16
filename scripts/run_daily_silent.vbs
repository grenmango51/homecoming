' ==============================================================================
' Flight Finder - Silent Daily Scheduled Task Launcher
' Runs the daily scheduled runner completely hidden (no console window flashing).
' ==============================================================================
Option Explicit

Dim WshShell, fso, scriptDir, projectRoot, psScript, cmd
Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
projectRoot = fso.GetParentFolderName(scriptDir)
psScript = scriptDir & "\run_daily_scheduled.ps1"

cmd = "powershell.exe -ExecutionPolicy Bypass -NoProfile -NonInteractive -WindowStyle Hidden -File """ & psScript & """"
WshShell.CurrentDirectory = projectRoot
WshShell.Run cmd, 0, False
