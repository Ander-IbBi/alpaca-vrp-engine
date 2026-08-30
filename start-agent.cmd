@echo off
REM Double-click entry point for the VRP Engine agent loop.
REM Windows opens .ps1 files in an editor rather than running them, so the launcher
REM needs this .cmd in front of scripts\start-agent.ps1.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-agent.ps1" %*
echo.
pause
