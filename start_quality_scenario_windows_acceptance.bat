@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1

for %%I in ("%~dp0.") do set "PACKAGE_ROOT=%%~fI"
cd /d "%PACKAGE_ROOT%"

echo ============================================================
echo Quality Scenario Windows Compatibility Acceptance
echo ============================================================
echo PACKAGE_ROOT=%PACKAGE_ROOT%
if "%~1"=="" (
  echo MODE=TEST_ONLY_CONTROLLED_DATA
) else (
  echo MODE=ORIGINAL_DB_COPY
  echo ORIGINAL_DB=%~f1
)
echo REAL_PROVIDER_SEMANTIC_GATE=OUT_OF_SCOPE
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
if "%~1"=="" (
  "%VENV_PYTHON%" "%PACKAGE_ROOT%\tools\run_quality_scenario_windows_acceptance.py" %SMOKE_FLAGS%
) else (
  "%VENV_PYTHON%" "%PACKAGE_ROOT%\tools\run_quality_scenario_windows_acceptance.py" --source-db "%~f1" %SMOKE_FLAGS%
)
set "EXIT_CODE=%errorlevel%"

echo.
echo WINDOWS_ACCEPTANCE_EXIT=%EXIT_CODE%
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
