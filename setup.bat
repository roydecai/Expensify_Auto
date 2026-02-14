@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion
set "SCRIPT_DIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%setup.ps1"
set EXITCODE=%ERRORLEVEL%
if %EXITCODE% NEQ 0 (
    echo Setup failed with error code %EXITCODE%.
    pause
    exit /b %EXITCODE%
)
echo Setup completed.
pause
endlocal
