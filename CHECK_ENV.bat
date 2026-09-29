@echo off
setlocal
cd /d "%~dp0"
set MODE=%~1
if "%MODE%"=="" set MODE=all

set "PYTHON_CMD="
where python >nul 2>nul
if %errorlevel%==0 set "PYTHON_CMD=python"
if not defined PYTHON_CMD (
  where py >nul 2>nul
  if %errorlevel%==0 set "PYTHON_CMD=py"
)
if not defined PYTHON_CMD (
  echo [FAIL] Python 3.11+ not found on PATH.
  set EXIT_CODE=2
  goto :finish
)

%PYTHON_CMD% scripts\hardware_case_precheck.py --mode %MODE%
set EXIT_CODE=%errorlevel%

:finish
echo.
if not "%HARDWARE_CASE_NO_PAUSE%"=="1" pause
endlocal & exit /b %EXIT_CODE%
