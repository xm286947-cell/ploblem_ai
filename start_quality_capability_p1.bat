@echo off
setlocal
cd /d "%~dp0"
echo Starting Quality Issue Analysis Engine with QualityScenario V1 preview...
echo Open http://127.0.0.1:8080/issues after startup.

if defined LEGACY_QUALITY_ISSUE_DB_PATH (
  set "QUALITY_DB=%LEGACY_QUALITY_ISSUE_DB_PATH%"
) else (
  set "QUALITY_DB=knowledge\quality_issue_v1.db"
)

echo Mature host DB: %QUALITY_DB%
if defined QUALITY_SCENARIO_V1_DB_PATH (
  echo QualityScenario V1 DB: %QUALITY_SCENARIO_V1_DB_PATH%
) else (
  echo QualityScenario V1 DB: same as mature host DB
)

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" main.py knowledge-web --db "%QUALITY_DB%" %*
) else (
  where py >nul 2>nul
  if not errorlevel 1 (
    py -3.12 -c "import sys" >nul 2>nul
    if not errorlevel 1 (
      py -3.12 main.py knowledge-web --db "%QUALITY_DB%" %*
    ) else (
      py -3.11 -c "import sys" >nul 2>nul
      if not errorlevel 1 (
        py -3.11 main.py knowledge-web --db "%QUALITY_DB%" %*
      ) else (
        py main.py knowledge-web --db "%QUALITY_DB%" %*
      )
    )
  ) else (
    python main.py knowledge-web --db "%QUALITY_DB%" %*
  )
)

set EXIT_CODE=%errorlevel%
echo.
if not "%REPEAT_CASE_NO_PAUSE%"=="1" pause
endlocal & exit /b %EXIT_CODE%
