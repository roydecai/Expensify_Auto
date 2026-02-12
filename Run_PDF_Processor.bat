@echo off
REM Launcher for the PDF Invoice Processor
REM Usage: Run_PDF_Processor.bat [arguments]

cd /d "%~dp0"
if "%~1"=="" (
    python "invoice_processor\run.py" "invoice_to_proceed"
) else (
    python "invoice_processor\run.py" %*
)

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Execution failed with error code %ERRORLEVEL%.
    pause
)
