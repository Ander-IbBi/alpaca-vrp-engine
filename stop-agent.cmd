@echo off
REM Double-click exit point: stops the agent loop, its restart wrapper and the panel.
REM Windows opens .ps1 files in an editor rather than running them, so the launcher
REM needs this .cmd in front of scripts\stop-agent.ps1.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop-agent.ps1" %*
echo.
pause
