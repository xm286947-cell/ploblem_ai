@echo off
setlocal
cd /d "%~dp0"
echo Starting Quality Issue Analysis Engine (integrated stable UI)...
echo Open http://127.0.0.1:8080/issues after startup.

if defined LEGACY_QUALITY_ISSUE_DB_PATH (
  set "QUALITY_DB=%LEGACY_QUALITY_ISSUE_DB_PATH%"
) else (
  set "QUALITY_DB=knowledge\quality_issue_v1.db"
)

if not exist "%QUALITY_DB%" (
  echo MATURE_DB_BOUND=FAIL
  echo Legacy quality DB was not found: %QUALITY_DB%
  echo Set LEGACY_QUALITY_ISSUE_DB_PATH to the existing mature quality_issue_v1.db.
  exit /b 3
)

echo Legacy DB: %QUALITY_DB%

set "PYTHON_CMD="
if exist ".venv\Scripts\python.exe" (
  set "PYTHON_CMD=".venv\Scripts\python.exe""
) else (
  where py >nul 2>nul
  if not errorlevel 1 (
    py -3.12 -c "import sys" >nul 2>nul
    if not errorlevel 1 (
      set "PYTHON_CMD=py -3.12"
    ) else (
      py -3.11 -c "import sys" >nul 2>nul
      if not errorlevel 1 set "PYTHON_CMD=py -3.11"
    )
  )
)

if not defined PYTHON_CMD (
  where python3.12 >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=python3.12"
)
if not defined PYTHON_CMD (
  where python3.11 >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=python3.11"
)
if not defined PYTHON_CMD (
  where python >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD (
  echo No compatible Python was found. Python 3.12 or 3.11 is recommended.
  exit /b 2
)

%PYTHON_CMD% tools\verify_legacy_quality_db.py "%QUALITY_DB%"
if errorlevel 1 (
  echo STEP1 startup blocked: mature Quality Scenario / Portrait data is not available in the bound DB.
  exit /b %errorlevel%
)

%PYTHON_CMD% main.py knowledge-web --db "%QUALITY_DB%" %*
set EXIT_CODE=%errorlevel%
echo.
if not "%REPEAT_CASE_NO_PAUSE%"=="1" pause
endlocal & exit /b %EXIT_CODE%
