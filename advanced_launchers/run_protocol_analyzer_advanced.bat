@echo off
setlocal
cd /d "%~dp0\.."
set "PYEXE=.venv\Scripts\python.exe"
if not exist "%PYEXE%" set "PYEXE=python"
"%PYEXE%" protocol_analyzer.py %*
if errorlevel 1 pause
