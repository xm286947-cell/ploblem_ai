@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1
for %%I in ("%~dp0.") do set "PACKAGE_ROOT=%%~fI"
cd /d "%PACKAGE_ROOT%"

if exist "%PACKAGE_ROOT%\CONFIG_OVERALL_R2_WINDOWS.cmd" call "%PACKAGE_ROOT%\CONFIG_OVERALL_R2_WINDOWS.cmd"
if "%LEGACY_QUALITY_ISSUE_DB_PATH%"=="" (
  echo [config] LEGACY_QUALITY_ISSUE_DB_PATH is required.
  echo Copy CONFIG_OVERALL_R2_WINDOWS.cmd.template to CONFIG_OVERALL_R2_WINDOWS.cmd
  echo and bind the approved existing Quality Issue database.
  if not "%OVERALL_R2_NO_PAUSE%"=="1" pause
  exit /b 6
)

if not exist "%PACKAGE_ROOT%\.venv\Scripts\python.exe" (
  call "%PACKAGE_ROOT%\INSTALL_OVERALL_R2_WINDOWS.bat"
  if errorlevel 1 exit /b %errorlevel%
)

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONPATH=%PACKAGE_ROOT%"
set "VENV_PYTHON=%PACKAGE_ROOT%\.venv\Scripts\python.exe"

echo Starting Overall R2 Complete Product Candidate...
echo URL: http://127.0.0.1:8080/p0/overall
"%VENV_PYTHON%" "%PACKAGE_ROOT%\scripts\overall_r2_windows_start.py" --package-root "%PACKAGE_ROOT%" %*
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" echo Startup failed. See %%LOCALAPPDATA%%\OverallR2\logs\overall-r2.log
if not "%OVERALL_R2_NO_PAUSE%"=="1" pause
endlocal & exit /b %EXIT_CODE%
