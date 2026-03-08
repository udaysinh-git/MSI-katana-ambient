@echo off
echo ============================================
echo   MSI Ambient Light Sync - Udaysinh-git
echo   NOTE: Must be run as Administrator!
echo ============================================
echo.
echo Installing requirements...
pip install -r requirements.txt
echo.
echo Starting MSI Ambient Light Sync...
python main.py
pause
