@echo off
cd /d "%~dp0"
set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
"%PYTHON_EXE%" --version >nul 2>nul
if errorlevel 1 set "PYTHON_EXE=python"
"%PYTHON_EXE%" -m src.pv_detail_app
pause
