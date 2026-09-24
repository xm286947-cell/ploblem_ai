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
echo Hardware Case Product Test V0.1.1
echo ==========================================
echo P07: http://127.0.0.1:8080/p0/hardware-cases/base-data
echo.
echo Starting browser and unified product Web...
start "" "http://127.0.0.1:8080/p0/hardware-cases/base-data"

where py >nul 2>nul
if %errorlevel%==0 (
  py main.py knowledge-p1-start --db "data\quality_capability_p1.db" --host 127.0.0.1 --port 8080
) else (
  python main.py knowledge-p1-start --db "data\quality_capability_p1.db" --host 127.0.0.1 --port 8080
)
set EXIT_CODE=%errorlevel%
echo.
pause
endlocal & exit /b %EXIT_CODE%
