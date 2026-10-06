@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul 2>&1
for %%I in ("%~dp0.") do set "PACKAGE_ROOT=%%~fI"
cd /d "%PACKAGE_ROOT%"

echo Starting Quality Issue Analysis Engine with QualityScenario V1 preview...
echo PACKAGE_ROOT=%PACKAGE_ROOT%
echo Open http://127.0.0.1:8080/issues after startup.

if defined LEGACY_QUALITY_ISSUE_DB_PATH (
  set "QUALITY_DB=%LEGACY_QUALITY_ISSUE_DB_PATH%"
) else (
  set "QUALITY_DB=%PACKAGE_ROOT%\knowledge\quality_issue_v1.db"
)

if defined QUALITY_SCENARIO_V1_DB_PATH (
  echo QualityScenario V1 DB: %QUALITY_SCENARIO_V1_DB_PATH%
) else (
  echo QualityScenario V1 DB: same as mature host DB
)

set "VENV_PYTHON=%PACKAGE_ROOT%\.venv\Scripts\python.exe"

if not exist "%VENV_PYTHON%" (
  echo Package-local .venv not found. Creating it now...
  set "BOOTSTRAP="
  where py >nul 2>nul
  if not errorlevel 1 (
    py -3.12 -c "import sys" >nul 2>nul
    if not errorlevel 1 set "BOOTSTRAP=py -3.12"
    if not defined BOOTSTRAP (
      py -3.11 -c "import sys" >nul 2>nul
      if not errorlevel 1 set "BOOTSTRAP=py -3.11"
    )
  )
  if not defined BOOTSTRAP (
    where python >nul 2>nul
    if not errorlevel 1 set "BOOTSTRAP=python"
  )
  if not defined BOOTSTRAP (
    echo Python >= 3.11 was not found.
    exit /b 2
  )
  !BOOTSTRAP! -m venv "%PACKAGE_ROOT%\.venv"
  if errorlevel 1 exit /b 2
)

echo VENV_PYTHON=%VENV_PYTHON%
"%VENV_PYTHON%" -c "import sys; print('PYTHON_EXECUTABLE=' + sys.executable); print('PYTHON_PREFIX=' + sys.prefix)"
if errorlevel 1 exit /b 2

"%VENV_PYTHON%" -c "import openpyxl; print('OPENPYXL_VERSION=' + openpyxl.__version__); print('OPENPYXL_FILE=' + openpyxl.__file__)"
if errorlevel 1 (
  echo OPENPYXL_IMPORT=FAIL
  echo The package-local venv is incomplete. Installing declared product dependencies...
  "%VENV_PYTHON%" -m pip install --disable-pip-version-check -r "%PACKAGE_ROOT%\requirements.txt" -r "%PACKAGE_ROOT%\requirements-runtime-p0-test.txt"
  if errorlevel 1 exit /b 2
  "%VENV_PYTHON%" -c "import openpyxl; print('OPENPYXL_VERSION=' + openpyxl.__version__); print('OPENPYXL_FILE=' + openpyxl.__file__)"
  if errorlevel 1 exit /b 2
)

echo DEPENDENCY_PREFLIGHT=PASS
"%VENV_PYTHON%" "%PACKAGE_ROOT%\tools\quality_scenario_real_data_preflight.py" --db "%QUALITY_DB%"
echo For Browser Golden, run the preflight with --require before testing.
"%VENV_PYTHON%" "%PACKAGE_ROOT%\main.py" knowledge-web --db "%QUALITY_DB%" %*
set "EXIT_CODE=%errorlevel%"
echo.
if not "%REPEAT_CASE_NO_PAUSE%"=="1" pause
endlocal & exit /b %EXIT_CODE%
