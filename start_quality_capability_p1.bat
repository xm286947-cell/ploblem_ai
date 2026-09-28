@echo off
setlocal
cd /d "%~dp0"
echo Starting Quality Issue Analysis Engine (integrated stable UI)...
echo Open http://127.0.0.1:8080/issues after startup.
set "QUALITY_DB=knowledge\quality_issue_v1.db"
set "LEGACY_QUALITY_ISSUE_DB_PATH=%QUALITY_DB%"
set "P0_DB=knowledge\quality_capability_p1.db"
if not exist "%QUALITY_DB%" (
  echo ERROR: Existing issue database was not found: %QUALITY_DB%
  echo Restore the original knowledge\quality_issue_v1.db before starting.
  if not "%REPEAT_CASE_NO_PAUSE%"=="1" pause
  endlocal & exit /b 2
)
where py >nul 2>nul
if %errorlevel%==0 (
  py main.py knowledge-p1-start --db "%P0_DB%" --host 127.0.0.1 --port 8080 %*
) else (
  python main.py knowledge-p1-start --db "%P0_DB%" --host 127.0.0.1 --port 8080 %*
)
set EXIT_CODE=%errorlevel%
echo.
if not "%REPEAT_CASE_NO_PAUSE%"=="1" pause
endlocal & exit /b %EXIT_CODE%
