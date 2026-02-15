@echo off
echo Stopping any running uvicorn processes...
taskkill /F /IM python.exe /FI "WINDOWTITLE eq uvicorn*" 2>nul
timeout /t 2 /nobreak >nul

echo Starting FastAPI server...
cd /d "%~dp0"
uvicorn main:app --reload
