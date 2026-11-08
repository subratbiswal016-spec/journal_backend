@echo off
REM One-click launcher for the OI dashboard (Windows).
cd /d "%~dp0"

if not exist ".venv\" (
    echo Creating virtual environment...
    python -m venv .venv
)

call .venv\Scripts\activate.bat

echo Installing / checking dependencies...
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt

if not exist ".env" (
    copy .env.example .env >nul
    echo Created .env from .env.example
)

echo.
echo ============================================
echo   OI Dashboard running at http://127.0.0.1:8787
echo   Press Ctrl+C to stop.
echo ============================================
echo.

python -m uvicorn backend.app:app --host 127.0.0.1 --port 8787
