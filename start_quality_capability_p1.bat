@echo off
setlocal
cd /d "%~dp0"
echo Starting Quality Issue Analysis Engine (integrated stable UI)...
echo Open http://127.0.0.1:8080/issues after startup.
set "QUALITY_DB=knowledge\quality_issue_v1.db"
where py >nul 2>nul
if %errorlevel%==0 (
  py main.py knowledge-web --db "%QUALITY_DB%" %*
) else (
  python main.py knowledge-web --db "%QUALITY_DB%" %*
)
set EXIT_CODE=%errorlevel%
echo.
if not "%REPEAT_CASE_NO_PAUSE%"=="1" pause
endlocal & exit /b %EXIT_CODE%
