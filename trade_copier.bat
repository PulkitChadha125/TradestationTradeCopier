@echo off
setlocal

REM Move to project directory (where this bat file is located)
cd /d "%~dp0"

echo ==========================================
echo   TradeStation Trade Copier Launcher
echo ==========================================

REM Check if .venv exists and has Python
if exist ".venv\Scripts\python.exe" (
    echo [OK] Virtual environment found.
) else (
    echo [INFO] Virtual environment not found. Creating .venv...
    py -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

echo [INFO] Installing requirements...
".venv\Scripts\python.exe" -m pip install -r "requirements.txt"
if errorlevel 1 (
    echo [ERROR] Failed to install requirements.
    pause
    exit /b 1
)

echo [INFO] Opening browser...
start "" "http://127.0.0.1:5000"

echo [INFO] Starting Flask app...
".venv\Scripts\python.exe" "app.py"

endlocal
