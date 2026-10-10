@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1
for %%I in ("%~dp0.") do set "PACKAGE_ROOT=%%~fI"
cd /d "%PACKAGE_ROOT%"

echo ============================================================
echo Quality Scenario Windows - CONTROLLED MOCK acceptance ONLY
echo NOT REAL AI: This entry cannot prove actual quality scenario generation.
echo For real model business evaluation use:
echo   start_quality_scenario_windows_real_provider.bat
echo ============================================================
echo SIMPLE MODE
echo 1. Put your existing DB in this package folder.
echo 2. Name it: quality_issue_v1.db
echo 3. Double-click this BAT.
echo.
echo You can also drag any DB file onto this BAT.
echo The original DB is copied internally before testing.
echo.

set "ORIGINAL_DB=%~1"
if not defined ORIGINAL_DB (
  if exist "%PACKAGE_ROOT%\quality_issue_v1.db" (
    set "ORIGINAL_DB=%PACKAGE_ROOT%\quality_issue_v1.db"
  )
)

if not defined ORIGINAL_DB (
  echo ORIGINAL_DB_NOT_FOUND
  echo.
  echo Copy your existing DB to:
  echo   %PACKAGE_ROOT%\quality_issue_v1.db
  echo.
  echo Or drag the DB file onto this BAT.
  pause
  exit /b 2
)

if not exist "%ORIGINAL_DB%" (
  echo ORIGINAL_DB_NOT_FOUND=%ORIGINAL_DB%
  pause
  exit /b 2
)

echo ORIGINAL_DB=%ORIGINAL_DB%
call "%PACKAGE_ROOT%\start_quality_scenario_windows_acceptance.bat" "%ORIGINAL_DB%"
endlocal
