@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "PIP_PROGRESS_BAR=off"
for %%I in ("%~dp0.") do set "PACKAGE_ROOT=%%~fI"
cd /d "%PACKAGE_ROOT%"
echo ============================================================
echo Quality Scenario - REAL PROVIDER + ISOLATED DATABASE
echo ============================================================
echo Source DB is snapshot-copied via SQLite backup; original is never written.
echo Review candidates in the isolated real-provider store survive restart.
echo Requires an already authorized model configuration and environment.
set "ORIGINAL_DB=%~1"
if not defined ORIGINAL_DB if exist "%PACKAGE_ROOT%\quality_issue_v1.db" set "ORIGINAL_DB=%PACKAGE_ROOT%\quality_issue_v1.db"
if not defined ORIGINAL_DB (
  echo ORIGINAL_DB_NOT_FOUND - drag an existing DB onto this BAT.
  if not defined CI pause
  exit /b 2
)
if not exist "%ORIGINAL_DB%" (
  echo ORIGINAL_DB_NOT_FOUND
  if not defined CI pause
  exit /b 2
)
set "VENV_PYTHON=%PACKAGE_ROOT%\.venv\Scripts\python.exe"
if not exist "%VENV_PYTHON%" (
  echo Python environment missing. Run start_quality_scenario_windows_acceptance.bat once to install dependencies.
  if not defined CI pause
  exit /b 2
)
"%VENV_PYTHON%" -c "import fastapi, openpyxl, yaml" >nul 2>nul
if errorlevel 1 (
  echo DEPENDENCIES_NOT_READY
  if not defined CI pause
  exit /b 3
)
echo REAL_PROVIDER_MODE=YES
"%VENV_PYTHON%" "%PACKAGE_ROOT%\tools\run_quality_scenario_windows_acceptance.py" --real-provider --source-db "%ORIGINAL_DB%"
set "EXIT_CODE=%errorlevel%"
echo REAL_PROVIDER_SESSION_EXIT=%EXIT_CODE%
if not defined CI pause
endlocal & exit /b %EXIT_CODE%
