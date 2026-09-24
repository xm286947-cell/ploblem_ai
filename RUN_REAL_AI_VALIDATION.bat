@echo off
setlocal
cd /d "%~dp0"

if "%HARDWARE_CASE_MODEL_CONFIG%"=="" set "HARDWARE_CASE_MODEL_CONFIG=%CD%\config\runtime\model.local.yaml"
if "%HARDWARE_CASE_RUNTIME_DB%"=="" set "HARDWARE_CASE_RUNTIME_DB=%CD%\data\runtime\hardware_case_runtime.db"

if not exist "config\hardware_case_real_validation.local.json" (
  echo [BLOCKED] config\hardware_case_real_validation.local.json is missing.
  echo Run INIT_LOCAL_CONFIG.bat first, then edit the local validation config.
  pause
  exit /b 2
)

set HARDWARE_CASE_NO_PAUSE=1
call CHECK_ENV.bat real-ai
if errorlevel 1 (
  echo.
  echo [BLOCKED] Real AI precheck failed.
  pause
  exit /b 2
)

echo.
echo Running company-local Hardware Case Real AI validation...
echo Real source content stays in this machine. The report contains aggregate metrics only.
echo.

where py >nul 2>nul
if %errorlevel%==0 (
  py tools\hardware_case_real_validation.py --config "config\hardware_case_real_validation.local.json"
) else (
  python tools\hardware_case_real_validation.py --config "config\hardware_case_real_validation.local.json"
)
set EXIT_CODE=%errorlevel%
echo.
if %EXIT_CODE%==0 (
  echo [PASS] Real AI validation command completed.
) else (
  echo [FAILED] Real AI validation command failed with exit code %EXIT_CODE%.
)
pause
endlocal & exit /b %EXIT_CODE%
