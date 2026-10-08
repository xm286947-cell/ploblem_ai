@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1
for %%I in ("%~dp0.") do set "PACKAGE_ROOT=%%~fI"
set "PY=%PACKAGE_ROOT%\.venv\Scripts\python.exe"
set "ORIGINAL_COPY=%PACKAGE_ROOT%\validation\windows_acceptance\original_quality_db_copy.db"
set "FIXTURE_DB=%PACKAGE_ROOT%\validation\windows_acceptance\quality_scenario_source_fixture.db"

if exist "%ORIGINAL_COPY%.fixture.json" (
  set "DB=%ORIGINAL_COPY%"
  echo G5_MODE=ORIGINAL_DB_COPY
) else (
  set "DB=%FIXTURE_DB%"
  echo G5_MODE=CONTROLLED_FIXTURE
)

if not exist "%PY%" (
  echo WINDOWS_ACCEPTANCE_VENV_NOT_FOUND
  pause
  exit /b 2
)
if not exist "%DB%" (
  echo WINDOWS_ACCEPTANCE_FIXTURE_NOT_FOUND
  pause
  exit /b 3
)

"%PY%" "%PACKAGE_ROOT%\tools\build_quality_scenario_test_fixture.py" --db "%DB%" --advance-g5
set "EXIT_CODE=%errorlevel%"
echo.
if "%EXIT_CODE%"=="0" echo G5_SOURCE_REVISION_ADVANCED=PASS
pause
endlocal & exit /b %EXIT_CODE%
