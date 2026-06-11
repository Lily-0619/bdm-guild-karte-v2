@echo off
cd /d "%~dp0"
echo BDM Guild Karte Tool debug launcher
echo.
echo Project: %cd%
echo.
echo Checking python...
python --version
echo.
echo Starting app with python...
python src\app.py
if %errorlevel% neq 0 (
    echo.
    echo python failed. Trying py...
    py --version
    py src\app.py
)
echo.
echo If the app did not open, copy the messages above.
pause
