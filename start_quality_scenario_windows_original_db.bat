@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1
for %%I in ("%~dp0.") do set "PACKAGE_ROOT=%%~fI"
cd /d "%PACKAGE_ROOT%"

echo ============================================================
echo Quality Scenario Windows - Original DB Acceptance
echo ============================================================
echo This mode COPIES the original DB first.
echo The original DB file will not be modified.
echo.

set "ORIGINAL_DB=%~1"
if not defined ORIGINAL_DB (
  set /p "ORIGINAL_DB=Please enter the full path of the original Quality DB: "
)
if not defined ORIGINAL_DB (
  echo ORIGINAL_DB_REQUIRED
  pause
  exit /b 2
)
if not exist "%ORIGINAL_DB%" (
  echo ORIGINAL_DB_NOT_FOUND=%ORIGINAL_DB%
  pause
  exit /b 2
)

call "%PACKAGE_ROOT%\start_quality_scenario_windows_acceptance.bat" "%ORIGINAL_DB%"
endlocal
