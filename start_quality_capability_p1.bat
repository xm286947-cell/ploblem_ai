@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1
cd /d "%~dp0"

echo ==========================================================
echo Mature Legacy Quality Scenario / Original Quality Platform
echo ==========================================================
set "QUALITY_DB=%LEGACY_QUALITY_ISSUE_DB_PATH%"
if not defined QUALITY_DB set "QUALITY_DB=knowledge\quality_issue_v1.db"
if not exist "%QUALITY_DB%" (
  echo MATURE_DB_BOUND=FAIL
  echo REASON=ORIGINAL_DB_NOT_FOUND
  echo Please bind your existing mature quality_issue_v1.db.
  echo The application will NOT create an empty database in its place.
  if not defined CI pause
  exit /b 3
)

set "PYTHON_CMD=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON_CMD%" (
  echo Preparing isolated Python runtime...
  where py >nul 2>nul
  if not errorlevel 1 (
    py -3.12 -m venv ".venv" >nul 2>nul
    if errorlevel 1 py -3.11 -m venv ".venv" >nul 2>nul
  )
  if not exist "%PYTHON_CMD%" (
    where python >nul 2>nul
    if not errorlevel 1 python -m venv ".venv"
  )
  if not exist "%PYTHON_CMD%" (
    echo PYTHON_RUNTIME_NOT_AVAILABLE. Python 3.11 or 3.12 is required.
    if not defined CI pause
    exit /b 2
  )
)

"%PYTHON_CMD%" -c "import fastapi,openpyxl,yaml,httpx" >nul 2>nul
if errorlevel 1 (
  echo Installing declared dependencies into local .venv...
  "%PYTHON_CMD%" -m pip install --disable-pip-version-check -r requirements.txt -r requirements-runtime-p0-test.txt
  if errorlevel 1 (
    echo DEPENDENCY_INSTALL_FAILED
    if not defined CI pause
    exit /b 2
  )
)

"%PYTHON_CMD%" tools\verify_legacy_quality_db.py "%QUALITY_DB%"
if errorlevel 1 (
  echo Original mature scenario data preflight failed; application not started.
  if not defined CI pause
  exit /b 4
)

echo MATURE_DB_BOUND=PASS
echo PRODUCT_URL=http://127.0.0.1:8080/quality-scenarios
echo Running old quality platform; no new QSV1 or Mock Provider.
"%PYTHON_CMD%" main.py knowledge-web --db "%QUALITY_DB%" %*
set "EXIT_CODE=%errorlevel%"
echo.
if not defined CI pause
endlocal & exit /b %EXIT_CODE%
