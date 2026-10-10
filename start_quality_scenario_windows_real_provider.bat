@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "PIP_PROGRESS_BAR=off"

for %%I in ("%~dp0.") do set "PACKAGE_ROOT=%%~fI"
cd /d "%PACKAGE_ROOT%"

echo ============================================================
echo Quality Scenario Windows - Real Provider / Isolated Database
echo ============================================================
echo PACKAGE_ROOT=%PACKAGE_ROOT%
set "ORIGINAL_DB=%~1"
if not defined ORIGINAL_DB if exist "%PACKAGE_ROOT%\quality_issue_v1.db" set "ORIGINAL_DB=%PACKAGE_ROOT%\quality_issue_v1.db"
if not defined ORIGINAL_DB (
  echo REAL_PROVIDER_SOURCE_DB_REQUIRED - drag your existing database onto this BAT.
  if not defined CI pause
  exit /b 2
)
if not exist "%ORIGINAL_DB%" (
  echo REAL_PROVIDER_SOURCE_DB_NOT_FOUND
  if not defined CI pause
  exit /b 2
)
echo REAL_PROVIDER_MODE=YES
echo ORIGINAL_DB_IS_READ_ONLY_AND_COPIED=YES
echo REAL_PROVIDER_REQUIRES_EXISTING_AUTHORIZED_MODEL_CONFIG=YES
echo REAL_BUSINESS_ACCEPTANCE=PENDING
echo.

set "VENV_PYTHON=%PACKAGE_ROOT%\.venv\Scripts\python.exe"

if not exist "%VENV_PYTHON%" (
  call :create_venv
  if errorlevel 1 (
    echo VENV_CREATE=FAIL
    if not defined CI pause
    exit /b 2
  )
)

"%VENV_PYTHON%" -c "import fastapi, openpyxl, yaml" >nul 2>nul
if errorlevel 1 (
  echo Installing declared dependencies...
  "%VENV_PYTHON%" -m pip install --disable-pip-version-check -r "%PACKAGE_ROOT%\requirements.txt" -r "%PACKAGE_ROOT%\requirements-runtime-p0-test.txt"
  if errorlevel 1 (
    echo DEPENDENCY_INSTALL=FAIL
    if not defined CI pause
    exit /b 3
  )
)

echo DEPENDENCY_PREFLIGHT=PASS
echo.
set "SMOKE_FLAGS="
if /I "%QS_WINDOWS_ACCEPTANCE_SMOKE_ONLY%"=="1" set "SMOKE_FLAGS=--smoke-only --no-browser"
"%VENV_PYTHON%" "%PACKAGE_ROOT%\tools\run_quality_scenario_windows_acceptance.py" --real-provider --source-db "%ORIGINAL_DB%" %SMOKE_FLAGS%
set "EXIT_CODE=%errorlevel%"

echo.
echo REAL_PROVIDER_SESSION_EXIT=%EXIT_CODE%
if not defined CI pause
endlocal & exit /b %EXIT_CODE%


:create_venv
echo Creating package-local Python environment...
where py >nul 2>nul
if not errorlevel 1 (
  py -3.12 -m venv "%PACKAGE_ROOT%\.venv"
  if not errorlevel 1 exit /b 0
  py -3.11 -m venv "%PACKAGE_ROOT%\.venv"
  if not errorlevel 1 exit /b 0
)
where python >nul 2>nul
if not errorlevel 1 (
  python -c "import sys; assert sys.version_info[:2] in [(3,11),(3,12)]" >nul 2>nul
  if not errorlevel 1 (
    python -m venv "%PACKAGE_ROOT%\.venv"
    if not errorlevel 1 exit /b 0
  )
)
echo PYTHON_311_312_NOT_FOUND
exit /b 2
