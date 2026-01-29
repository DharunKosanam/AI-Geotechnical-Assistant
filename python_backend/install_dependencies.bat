@echo off
echo ========================================
echo Installing Python Dependencies
echo ========================================
echo.

echo Installing packages from requirements.txt...
pip install -r requirements.txt

echo.
echo ========================================
echo Verifying Installation
echo ========================================
echo.

python -c "from PIL import Image; import img2pdf; print('✅ Pillow installed successfully')"
python -c "from PIL import Image; import img2pdf; print('✅ img2pdf installed successfully')"
python -c "import httpx; print('✅ httpx installed successfully')"

echo.
echo ========================================
echo Installation Complete!
echo ========================================
echo.
echo You can now run the server with:
echo   python main.py
echo.
echo Or with uvicorn:
echo   uvicorn main:app --reload --host 0.0.0.0 --port 8000
echo.

pause

