@echo off
rem Launch the BDM Discord Bot controller from the project root.
rem Run via -m so that the project root is on sys.path (fixes "No module named 'src'").
cd /d "%~dp0"
set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
"%PYTHON_EXE%" --version >nul 2>nul
if errorlevel 1 set "PYTHON_EXE=python"
"%PYTHON_EXE%" -X utf8 -m bot.bot_app
pause
