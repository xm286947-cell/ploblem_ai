@echo off
setlocal
cd /d "%~dp0"
echo Starting Quality Capability P1...
echo Open http://127.0.0.1:8080/p0/issues after startup.
set "QUALITY_DB=knowledge\quality_capability_p1.db"
if not exist "%QUALITY_DB%" if exist "knowledge\quality_capability_p0.db" set "QUALITY_DB=knowledge\quality_capability_p0.db"
where py >nul 2>nul
if %errorlevel%==0 (
  py main.py knowledge-p1-start --db "%QUALITY_DB%" %*
) else (
  python main.py knowledge-p1-start --db "%QUALITY_DB%" %*
)
set EXIT_CODE=%errorlevel%
echo.
if not "%REPEAT_CASE_NO_PAUSE%"=="1" pause
endlocal & exit /b %EXIT_CODE%
