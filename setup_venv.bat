@echo off
setlocal
cd /d "%~dp0"

echo ============================================================================
echo MHO984 Toolkit Public Beta - local Python environment setup
echo ============================================================================
echo This creates .venv inside this folder. It does NOT upgrade global packages.
echo.

set "PYBASE="
where py >nul 2>nul && set "PYBASE=py -3"
if not defined PYBASE (
  where python >nul 2>nul && set "PYBASE=python"
)
if not defined PYBASE (
  echo Python 3 was not found. Install Python from python.org and retry.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  %PYBASE% -m venv .venv
  if errorlevel 1 goto :fail
)

".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :fail
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :fail

echo.
echo Setup complete.
echo Next: START_MHO984_Toolkit.bat
pause
exit /b 0

:fail
echo.
echo Setup failed. See the messages above.
pause
exit /b 1
