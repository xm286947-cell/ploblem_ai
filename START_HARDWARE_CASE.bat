@echo off
setlocal
cd /d "%~dp0"

if not exist "data" mkdir data
if not exist "data\runtime" mkdir data\runtime

set HARDWARE_CASE_NO_PAUSE=1
call CHECK_ENV.bat web
if errorlevel 1 (
  echo.
  echo [BLOCKED] Web precheck failed.
  pause
  exit /b 2
)

echo.
echo Hardware Case Product Test · Full Frontend
echo ==========================================
echo P01: http://127.0.0.1:8080/p0/hardware-cases
echo.
echo Starting browser and unified product Web...
start "" "http://127.0.0.1:8080/p0/hardware-cases"

where py >nul 2>nul
if %errorlevel%==0 (
  py scripts\hardware_case_web_start.py --db "data\quality_capability_p1.db" --hardware-db "data\hardware_case_mvp.db" --tree-upload-dir "data\hardware_case_tree_uploads" --source-root "data\hardware_case_sources" --host 127.0.0.1 --port 8080
) else (
  python scripts\hardware_case_web_start.py --db "data\quality_capability_p1.db" --hardware-db "data\hardware_case_mvp.db" --tree-upload-dir "data\hardware_case_tree_uploads" --host 127.0.0.1 --port 8080
)
set EXIT_CODE=%errorlevel%
echo.
pause
endlocal & exit /b %EXIT_CODE%
