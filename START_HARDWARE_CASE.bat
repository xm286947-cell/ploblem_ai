@echo off
setlocal
cd /d "%~dp0"

if not defined HARDWARE_CASE_NO_PAUSE set "HARDWARE_CASE_NO_PAUSE=0"
call INIT_LOCAL_CONFIG.bat
call CHECK_ENV.bat web
if errorlevel 1 (
  echo.
  echo [BLOCKED] Web precheck failed.
  if not "%HARDWARE_CASE_NO_PAUSE%"=="1" pause
  exit /b 2
)

echo.
echo Hardware Case Product Test · Full Frontend
echo ==========================================
if not defined HARDWARE_CASE_HOST set "HARDWARE_CASE_HOST=127.0.0.1"
if not defined HARDWARE_CASE_PORT set "HARDWARE_CASE_PORT=8080"
echo P01: http://%HARDWARE_CASE_HOST%:%HARDWARE_CASE_PORT%/p0/hardware-cases
echo.
echo Starting unified product Web...
if not "%HARDWARE_CASE_NO_BROWSER%"=="1" start "" "http://%HARDWARE_CASE_HOST%:%HARDWARE_CASE_PORT%/p0/hardware-cases"

set "PYTHON_CMD="
where python >nul 2>nul
if %errorlevel%==0 set "PYTHON_CMD=python"
if not defined PYTHON_CMD (
  where py >nul 2>nul
  if %errorlevel%==0 set "PYTHON_CMD=py"
)
if not defined PYTHON_CMD (
  echo [BLOCKED] Python 3.11+ not found on PATH.
  if not "%HARDWARE_CASE_NO_PAUSE%"=="1" pause
  exit /b 2
)

%PYTHON_CMD% scripts\hardware_case_web_start.py --host %HARDWARE_CASE_HOST% --port %HARDWARE_CASE_PORT%
set EXIT_CODE=%errorlevel%
echo.
if not "%HARDWARE_CASE_NO_PAUSE%"=="1" pause
endlocal & exit /b %EXIT_CODE%
