@echo off
setlocal
cd /d "%~dp0"

set "PYEXE="
if exist ".venv\Scripts\pythonw.exe" set "PYEXE=.venv\Scripts\pythonw.exe"
if not defined PYEXE if exist ".venv\Scripts\python.exe" set "PYEXE=.venv\Scripts\python.exe"
if not defined PYEXE where pythonw >nul 2>nul && set "PYEXE=pythonw"
if not defined PYEXE where python >nul 2>nul && set "PYEXE=python"

if not defined PYEXE (
  echo Python 3 was not found.
  echo Install Python 3 from python.org, then run this file again.
  pause
  exit /b 1
)

start "" "%PYEXE%" "%~dp0mho984_toolkit_main.py"
exit /b 0
