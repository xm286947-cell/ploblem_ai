@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1

for %%I in ("%~dp0.") do set "PACKAGE_ROOT=%%~fI"
cd /d "%PACKAGE_ROOT%" || exit /b 2

set "LEGACY_FIXTURE_DB=%PACKAGE_ROOT%\validation\quality_scenario_w4_fixture.db"
set "P0_DB=%PACKAGE_ROOT%\validation\overall_current_platform_p0.db"
set "VENV_PYTHON=%PACKAGE_ROOT%\.venv\Scripts\python.exe"

if not exist "%LEGACY_FIXTURE_DB%" (
  echo OVERALL_TEST_FIXTURE_MISSING=%LEGACY_FIXTURE_DB%
  exit /b 3
)

set "LEGACY_QUALITY_ISSUE_DB_PATH=%LEGACY_FIXTURE_DB%"
set "PYTHONPATH=%PACKAGE_ROOT%"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

echo PACKAGE_MODE=OVERALL_CURRENT_PLATFORM
echo DEFAULT_ENTRY=http://127.0.0.1:18080/p0/overall
echo LEGACY_QUALITY_ISSUE_DB_PATH=%LEGACY_QUALITY_ISSUE_DB_PATH%
echo P0_DB=%P0_DB%
echo QS_W4_SYNTHETIC_SOURCE_FIXTURE=YES
echo QS_W4_FIXTURE_IS_SUPPORTING_TEST_DATA=YES
echo DIRECT_QSV1_CANDIDATE_WRITE=NO
echo DIRECT_QSV1_PUBLISH_WRITE=NO

if exist "%VENV_PYTHON%" goto VENV_READY

echo Package-local .venv not found. Creating it now...

where py >nul 2>nul
if not errorlevel 1 (
  py -3.12 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>nul
  if not errorlevel 1 goto CREATE_VENV_312
  py -3.11 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>nul
  if not errorlevel 1 goto CREATE_VENV_311
)

where python >nul 2>nul
if not errorlevel 1 (
  python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>nul
  if not errorlevel 1 goto CREATE_VENV_PYTHON
)

echo Python >= 3.11 was not found.
exit /b 2

:CREATE_VENV_312
py -3.12 -m venv "%PACKAGE_ROOT%\.venv"
if errorlevel 1 exit /b 2
goto VENV_READY

:CREATE_VENV_311
py -3.11 -m venv "%PACKAGE_ROOT%\.venv"
if errorlevel 1 exit /b 2
goto VENV_READY

:CREATE_VENV_PYTHON
python -m venv "%PACKAGE_ROOT%\.venv"
if errorlevel 1 exit /b 2

:VENV_READY
"%VENV_PYTHON%" -c "import sys; assert sys.version_info >= (3,11); print('PYTHON_EXECUTABLE=' + sys.executable); print('PYTHON_VERSION=' + sys.version.split()[0])"
if errorlevel 1 exit /b 2

"%VENV_PYTHON%" -c "import fastapi, openpyxl, uvicorn; print('OVERALL_DEPENDENCY_IMPORT=PASS')" >nul 2>&1
if errorlevel 1 (
  echo Installing declared product dependencies...
  "%VENV_PYTHON%" -m pip install --disable-pip-version-check -r "%PACKAGE_ROOT%\requirements.txt" -r "%PACKAGE_ROOT%\requirements-runtime-p0-test.txt"
  if errorlevel 1 exit /b 2
)

"%VENV_PYTHON%" -c "import fastapi, openpyxl, uvicorn; print('OVERALL_DEPENDENCY_IMPORT=PASS')"
if errorlevel 1 exit /b 2

"%VENV_PYTHON%" "%PACKAGE_ROOT%\main.py" knowledge-p1-start --db "%P0_DB%" %*
set "EXIT_CODE=%ERRORLEVEL%"

endlocal & exit /b %EXIT_CODE%
