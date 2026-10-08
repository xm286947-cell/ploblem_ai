@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1

for %%I in ("%~dp0.") do set "PACKAGE_ROOT=%%~fI"
cd /d "%PACKAGE_ROOT%"

echo ============================================================
echo Quality Scenario Windows Compatibility Acceptance
echo ============================================================
echo PACKAGE_ROOT=%PACKAGE_ROOT%
echo MODE=TEST_ONLY_CONTROLLED_DATA
echo REAL_PROVIDER_SEMANTIC_GATE=OUT_OF_SCOPE
echo.

set "VENV_PYTHON=%PACKAGE_ROOT%\.venv\Scripts\python.exe"

if not exist "%VENV_PYTHON%" (
  echo Creating package-local Python environment...
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
    echo PYTHON_NOT_FOUND
    echo Python 3.11 or 3.12 is required.
    pause
    exit /b 2
  )
  %BOOTSTRAP% -m venv "%PACKAGE_ROOT%\.venv"
  if errorlevel 1 (
    echo VENV_CREATE=FAIL
    pause
    exit /b 2
  )
)

"%VENV_PYTHON%" -c "import fastapi, openpyxl, yaml" >nul 2>nul
if errorlevel 1 (
  echo Installing declared dependencies...
  "%VENV_PYTHON%" -m pip install --disable-pip-version-check -r "%PACKAGE_ROOT%\requirements.txt" -r "%PACKAGE_ROOT%\requirements-runtime-p0-test.txt"
  if errorlevel 1 (
    echo DEPENDENCY_INSTALL=FAIL
    pause
    exit /b 3
  )
)

echo DEPENDENCY_PREFLIGHT=PASS
echo.
"%VENV_PYTHON%" "%PACKAGE_ROOT%\tools\run_quality_scenario_windows_acceptance.py"
set "EXIT_CODE=%errorlevel%"

echo.
echo WINDOWS_ACCEPTANCE_EXIT=%EXIT_CODE%
pause
endlocal & exit /b %EXIT_CODE%
