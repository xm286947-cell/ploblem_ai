@echo off
setlocal
set "APP_DIR=%~dp0"
pushd "%APP_DIR%"

where py >nul 2>nul
if errorlevel 1 (
  where python >nul 2>nul
  if errorlevel 1 (
    echo Python 3.11 or newer is required. Install Python and retry.
    pause
    exit /b 2
  )
  set "PYTHON_CMD=python"
) else (
  set "PYTHON_CMD=py -3"
)

if not exist ".venv\Scripts\python.exe" (
  %PYTHON_CMD% -m venv ".venv"
  if errorlevel 1 goto :failed
)
".venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 (
  echo Python 3.11 or newer is required.
  goto :failed
)
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r "requirements-major-mvp-product.txt"
if errorlevel 1 goto :failed

if not exist "data\quality" mkdir "data\quality"
if not exist "data\major\attachments" mkdir "data\major\attachments"
if not exist "data\historical_case" mkdir "data\historical_case"
if not exist "data\logs" mkdir "data\logs"
if not defined MAJOR_MVP_PORT set "MAJOR_MVP_PORT=8080"
echo Major Production: http://127.0.0.1:%MAJOR_MVP_PORT%/p0/major-production
echo Historical Cases: http://127.0.0.1:%MAJOR_MVP_PORT%/p0/cases
echo Issues / Repeat Risk: http://127.0.0.1:%MAJOR_MVP_PORT%/p0/issues
".venv\Scripts\python.exe" "scripts\major_mvp_product_start.py" %*
set "EXIT_CODE=%ERRORLEVEL%"
popd
pause
exit /b %EXIT_CODE%

:failed
set "EXIT_CODE=%ERRORLEVEL%"
popd
pause
exit /b %EXIT_CODE%
