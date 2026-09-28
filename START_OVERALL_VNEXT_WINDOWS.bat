@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1
for %%I in ("%~dp0.") do set "PACKAGE_ROOT=%%~fI"
cd /d "%PACKAGE_ROOT%"

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONPATH=%PACKAGE_ROOT%"
set "STORAGE_LIFE_EXECUTION_MODE=runtime"
set "UNIFIED_AGENT_RUNTIME_ROOT=%PACKAGE_ROOT%"
set "STORAGE_MODEL_CONFIG=%PACKAGE_ROOT%\config\runtime\model.yaml"
set "STORAGE_MODEL_CONFIG_SOURCE=shared-runtime"

if not exist "%PACKAGE_ROOT%\.venv\Scripts\python.exe" (
  echo [setup] Creating package-local Python environment...
  where py >nul 2>nul
  if not errorlevel 1 (
    py -3.12 -m venv "%PACKAGE_ROOT%\.venv"
  ) else (
    where python >nul 2>nul
    if errorlevel 1 goto :python_missing
    python -m venv "%PACKAGE_ROOT%\.venv"
  )
  if errorlevel 1 goto :venv_failed
)

set "VENV_PYTHON=%PACKAGE_ROOT%\.venv\Scripts\python.exe"
echo [setup] Installing application dependencies...
if exist "%PACKAGE_ROOT%\.wheelhouse\" (
  "%VENV_PYTHON%" -m pip install --disable-pip-version-check --no-index --find-links "%PACKAGE_ROOT%\.wheelhouse" -r "%PACKAGE_ROOT%\requirements.txt" -r "%PACKAGE_ROOT%\requirements-runtime-p0-test.txt"
) else (
  "%VENV_PYTHON%" -m pip install --disable-pip-version-check -r "%PACKAGE_ROOT%\requirements.txt" -r "%PACKAGE_ROOT%\requirements-runtime-p0-test.txt"
)
if errorlevel 1 goto :dependency_failed

if not exist "%PACKAGE_ROOT%\logs" mkdir "%PACKAGE_ROOT%\logs" >nul 2>&1
echo [startup] Checking shared Runtime binding...
"%VENV_PYTHON%" "%PACKAGE_ROOT%\scripts\overall_vnext_windows_start.py" --check-runtime-only
if errorlevel 1 goto :runtime_failed

echo.
echo Starting Overall VNext on the existing single FastAPI host...
"%VENV_PYTHON%" "%PACKAGE_ROOT%\scripts\overall_vnext_windows_start.py" %*
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" echo Startup failed. Check "%LOCALAPPDATA%\OverallVNextDemo\logs\launcher.log"
if not "%OVERALL_VNEXT_NO_PAUSE%"=="1" pause
endlocal & exit /b %EXIT_CODE%

:python_missing
echo Python 3.12 x64 was not found. Install Python and enable the Python Launcher.
set "EXIT_CODE=2"
goto :finish_error
:venv_failed
echo Could not create the package-local virtual environment.
set "EXIT_CODE=3"
goto :finish_error
:dependency_failed
echo Dependency installation failed. Check wheelhouse/PyPI access and retry.
set "EXIT_CODE=4"
goto :finish_error
:runtime_failed
echo Runtime import source does not match the packaged shared Runtime. See the message above.
set "EXIT_CODE=5"
:finish_error
if not "%OVERALL_VNEXT_NO_PAUSE%"=="1" pause
endlocal & exit /b %EXIT_CODE%
