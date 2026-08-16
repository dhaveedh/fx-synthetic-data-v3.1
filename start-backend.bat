@echo off
echo ============================================
echo  FX Trade Generator - Backend
echo ============================================
echo.

:: Backend is chosen by .env (CLAUDE_BACKEND=bedrock/anthropic, auto-detects if unset)
:: See shared\claude_client.py for detection order.
echo.

:: Setup venv
if not exist "venv\Scripts\activate.bat" (
    echo  Creating virtual environment...
    python -m venv venv
)

call venv\Scripts\activate.bat
pip install -r requirements.txt -q

echo.
echo  Starting backend on http://127.0.0.1:8000
echo  API docs: http://127.0.0.1:8000/docs
echo  Press Ctrl+C to stop.
echo ============================================
echo.

uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload
