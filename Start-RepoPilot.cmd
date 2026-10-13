@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-RepoPilot.ps1"
if errorlevel 1 (
    echo.
    echo Startup failed. See the message above and press any key to close.
    pause >nul
    exit /b 1
)
