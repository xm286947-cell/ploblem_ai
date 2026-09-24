@echo off
setlocal
cd /d "%~dp0"
echo.
echo Hardware Case Product Test V0.1
echo ==========================================
echo Starting unified Quality Capability Web...
echo P07: http://127.0.0.1:8080/p0/hardware-cases/base-data
echo.
where py >nul 2>nul
if %errorlevel%==0 (
  py main.py knowledge-p1-start --db "knowledge\quality_capability_p1.db" --host 127.0.0.1 --port 8080
) else (
  python main.py knowledge-p1-start --db "knowledge\quality_capability_p1.db" --host 127.0.0.1 --port 8080
)
set EXIT_CODE=%errorlevel%
echo.
if not "%HARDWARE_CASE_NO_PAUSE%"=="1" pause
endlocal & exit /b %EXIT_CODE%
