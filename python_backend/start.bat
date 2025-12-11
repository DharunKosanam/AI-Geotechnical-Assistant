@echo off
REM Windows batch script to start the FastAPI server

echo Starting AI Geotechnical Chat - Python FastAPI Backend...
echo.

REM Check if virtual environment exists
if not exist "venv\" (
    echo Virtual environment not found. Creating one...
    python -m venv venv
    echo.
    echo Installing dependencies...
    call venv\Scripts\activate.bat
    pip install -r requirements.txt
    echo.
) else (
    call venv\Scripts\activate.bat
)

REM Check if .env exists
if not exist ".env" (
    echo.
    echo WARNING: .env file not found!
    echo Please copy env.example to .env and configure your environment variables.
    echo.
    pause
    exit /b 1
)

echo.
echo Starting server on http://localhost:8000
echo API Documentation: http://localhost:8000/docs
echo.
echo Press Ctrl+C to stop the server
echo.

python main.py

