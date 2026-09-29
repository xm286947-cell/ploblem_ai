@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1
for %%I in ("%~dp0.") do set "PACKAGE_ROOT=%%~fI"
cd /d "%PACKAGE_ROOT%"

if not exist "%PACKAGE_ROOT%\.venv\Scripts\python.exe" (
  echo [setup] Creating Overall R2 package-local Python 3.11 environment...
  where py >nul 2>nul
  if not errorlevel 1 (
    py -3.11 -m venv "%PACKAGE_ROOT%\.venv"
  ) else (
    where python >nul 2>nul
    if errorlevel 1 goto :python_missing
    python -m venv "%PACKAGE_ROOT%\.venv"
  )
  if errorlevel 1 goto :venv_failed
)

set "VENV_PYTHON=%PACKAGE_ROOT%\.venv\Scripts\python.exe"
echo [setup] Installing Overall R2 dependencies...
"%VENV_PYTHON%" -m pip install --disable-pip-version-check --upgrade pip
if errorlevel 1 goto :dependency_failed
"%VENV_PYTHON%" -m pip install --disable-pip-version-check -r "%PACKAGE_ROOT%\requirements.txt" -r "%PACKAGE_ROOT%\requirements-runtime-p0-test.txt"
if errorlevel 1 goto :dependency_failed

echo INSTALL_OVERALL_R2_WINDOWS=PASS
exit /b 0

:python_missing
echo Python 3.11 x64 was not found. Install Python 3.11 and enable the Python Launcher.
exit /b 2
:venv_failed
echo Could not create the package-local virtual environment.
exit /b 3
:dependency_failed
echo Dependency installation failed. Check PyPI/network access and retry.
exit /b 4
