@echo off
setlocal
cd /d "%~dp0"
set MODE=%~1
if "%MODE%"=="" set MODE=all

where py >nul 2>nul
if %errorlevel%==0 (
  py scripts\hardware_case_precheck.py --mode %MODE%
) else (
  python scripts\hardware_case_precheck.py --mode %MODE%
)
set EXIT_CODE=%errorlevel%
echo.
if not "%HARDWARE_CASE_NO_PAUSE%"=="1" pause
endlocal & exit /b %EXIT_CODE%
