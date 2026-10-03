@echo off
setlocal
cd /d "%~dp0.."
set "PYTHON_EXE=.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
"%PYTHON_EXE%" -u redecode_existing_dataset_r12_11e.py %*
set EXITCODE=%ERRORLEVEL%
pause
exit /b %EXITCODE%
