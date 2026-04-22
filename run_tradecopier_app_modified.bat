@echo off
setlocal

REM Run from project root (folder containing this file)
cd /d "%~dp0"

echo ==========================================
echo   TradeStation Copier (app_modified.py)
echo ==========================================

if exist ".venv\Scripts\python.exe" (
    echo [OK] Virtual environment found: .venv
) else (
    echo [INFO] .venv not found. Creating virtual environment...
    py -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create .venv.
        pause
        exit /b 1
    )
)

echo [INFO] Upgrading pip...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
    echo [WARN] pip upgrade failed. Continuing...
)

if exist "requirements.txt" (
    echo [INFO] Installing requirements from requirements.txt...
    ".venv\Scripts\python.exe" -m pip install -r "requirements.txt"
    if errorlevel 1 (
        echo [ERROR] Failed to install requirements.
        pause
        exit /b 1
    )
) else (
    echo [WARN] requirements.txt not found. Skipping dependency installation.
)

echo [INFO] Opening browser at http://127.0.0.1:5000 ...
start "" "http://127.0.0.1:5000"

echo [INFO] Starting orderbook copier app (app_modified.py)...
".venv\Scripts\python.exe" "app_modified.py"

endlocal
