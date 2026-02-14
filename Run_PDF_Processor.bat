@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
set SOURCE_DIR=invoice_to_proceed
set TARGET_DIR=invoice_to_submit
set LOG_DIR=logs
set OUTPUT_DIR=temp
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set PYTHON=
set SYSTEM_PYTHON=
set VENV_PYTHON=
set PY_CANDIDATE_1=%~dp0.venv\Scripts\python.exe
set PY_CANDIDATE_2=%~dp0venv\Scripts\python.exe
set PY_SYSTEM_FILE=%~dp0.python_path
set PY_VENV_FILE=%~dp0.venv_path
if exist "%PY_SYSTEM_FILE%" (
    for /f "usebackq delims=" %%P in ("%PY_SYSTEM_FILE%") do set SYSTEM_PYTHON=%%P
)
if exist "%PY_VENV_FILE%" (
    for /f "usebackq delims=" %%P in ("%PY_VENV_FILE%") do set VENV_PYTHON=%%P
)
if not defined VENV_PYTHON if exist "%PY_CANDIDATE_1%" set VENV_PYTHON=%PY_CANDIDATE_1%
if not defined VENV_PYTHON if exist "%PY_CANDIDATE_2%" set VENV_PYTHON=%PY_CANDIDATE_2%
if not defined SYSTEM_PYTHON (
    where python >nul 2>nul
    if not errorlevel 1 set SYSTEM_PYTHON=python
)
if defined SYSTEM_PYTHON (
    call :python_usable "%SYSTEM_PYTHON%"
    if "!PY_OK!"=="1" set PYTHON=%SYSTEM_PYTHON%
)
if not defined PYTHON if defined VENV_PYTHON (
    call :python_usable "%VENV_PYTHON%"
    if "!PY_OK!"=="1" set PYTHON=%VENV_PYTHON%
)
if not defined PYTHON if defined VENV_PYTHON set PYTHON=%VENV_PYTHON%
if not defined PYTHON (
    echo Python not found or requirements missing. Please run setup.bat.
    pause
    exit /b 2
)
if not exist "%SOURCE_DIR%" mkdir "%SOURCE_DIR%"
if not exist "%TARGET_DIR%" mkdir "%TARGET_DIR%"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo Using Python: %PYTHON%
echo [%date% %time%] Runner started > "%LOG_DIR%\runner.log"

:loop
set FOUND_PDF=0
for /f "delims=" %%F in ('dir /s /b "%SOURCE_DIR%\*.pdf" 2^>nul') do (
    set FOUND_PDF=1
    goto after_scan
)
:after_scan
if "!FOUND_PDF!"=="1" (
    echo [%date% %time%] PDF detected >> "%LOG_DIR%\runner.log"
    call "%PYTHON%" "src\gui_runner.py" "%SOURCE_DIR%" --output-dir "%OUTPUT_DIR%" --log-file "%LOG_DIR%\pdf_processor.log"
    set EXITCODE=!ERRORLEVEL!
    if !EXITCODE! NEQ 0 (
        echo Execution failed with error code !EXITCODE!.
        echo [%date% %time%] Invoice processor failed: !EXITCODE! >> "%LOG_DIR%\runner.log"
        type "%LOG_DIR%\pdf_processor.log"
        pause
    )
    
    call "%PYTHON%" "src\folder_transfer\run.py" "%SOURCE_DIR%" "%TARGET_DIR%"
    set EXITCODE=!ERRORLEVEL!
    if !EXITCODE! NEQ 0 (
        echo Transfer failed with error code !EXITCODE!.
        echo [%date% %time%] Folder transfer failed: !EXITCODE! >> "%LOG_DIR%\runner.log"
        pause
    )
)
timeout /t 600 /nobreak >nul
goto loop

:python_usable
set PY_OK=0
"%~1" -V >nul 2>&1
if errorlevel 1 goto :eof
"%~1" -c "import importlib.util;mods=['pdfplumber','pdf2image','PIL','dotenv'];missing=[m for m in mods if importlib.util.find_spec(m) is None];import sys;sys.exit(0 if not missing else 1)" >nul 2>&1
if errorlevel 1 goto :eof
set PY_OK=1
goto :eof
