@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "EXPECTED_SOURCE_SHA=55aaf9234ec102360043f4deef782126bc234b80"
set "APP_HOME=%LOCALAPPDATA%\MajorV11"
set "VENV_DIR=%APP_HOME%\venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "DATA_DIR=%APP_HOME%\data"
set "P0_DB=%DATA_DIR%\major_v11_p0.db"
set "WEB_ENTRY=http://127.0.0.1:18080/p0/major-production"

echo MAJOR_V11_WINDOWS_LAUNCHER=DELTA_B_GITHUB_NATIVE_TEST
echo EXPECTED_SOURCE_SHA=%EXPECTED_SOURCE_SHA%
echo WEB_ENTRY=%WEB_ENTRY%

set "ACTUAL_SOURCE_SHA="
if exist "PRODUCT_SOURCE_SHA" (
  set /p ACTUAL_SOURCE_SHA=<"PRODUCT_SOURCE_SHA"
) else (
  for /f "usebackq delims=" %%G in (`git rev-parse HEAD 2^>nul`) do set "ACTUAL_SOURCE_SHA=%%G"
)
if not defined ACTUAL_SOURCE_SHA (
  echo ERROR=SOURCE_IDENTITY_UNAVAILABLE
  exit /b 11
)
if /I not "!ACTUAL_SOURCE_SHA!"=="%EXPECTED_SOURCE_SHA%" (
  echo ERROR=SOURCE_IDENTITY_MISMATCH
  echo ACTUAL_SOURCE_SHA=!ACTUAL_SOURCE_SHA!
  exit /b 12
)
echo SOURCE_IDENTITY=PASS

if not exist "%APP_HOME%" mkdir "%APP_HOME%"
if not exist "%DATA_DIR%" mkdir "%DATA_DIR%"

if not exist "%VENV_PY%" (
  set "BASE_PY="
  where py >nul 2>&1
  if not errorlevel 1 (
    py -3.12 -c "import sys; raise SystemExit(0 if sys.version_info[:2]==(3,12) else 1)" >nul 2>&1 && set "BASE_PY=py -3.12"
    if not defined BASE_PY py -3.11 -c "import sys; raise SystemExit(0 if sys.version_info[:2]==(3,11) else 1)" >nul 2>&1 && set "BASE_PY=py -3.11"
  )
  if not defined BASE_PY (
    where python >nul 2>&1
    if not errorlevel 1 (
      python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1 && set "BASE_PY=python"
    )
  )
  if not defined BASE_PY (
    echo ERROR=PYTHON_3_11_OR_3_12_NOT_FOUND
    exit /b 21
  )
  echo CREATE_VENV=%VENV_DIR%
  !BASE_PY! -m venv "%VENV_DIR%"
  if errorlevel 1 exit /b 22
)

"%VENV_PY%" -c "import sys; assert sys.version_info >= (3,11); import fastapi, openpyxl, uvicorn" >nul 2>&1
if errorlevel 1 (
  echo DEPENDENCIES=INSTALLING
  "%VENV_PY%" -m pip install --disable-pip-version-check -r "%CD%\requirements.txt" -r "%CD%\requirements-runtime-p0-test.txt"
  if errorlevel 1 (
    echo ERROR=DEPENDENCY_INSTALL_FAILED
    exit /b 31
  )
)
echo DEPENDENCY_IMPORT=PASS

set "PYTHONPATH=%CD%"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

rem Persist safe Provider transport metadata only; not prompts, source text or credentials.
if not exist "%APP_HOME%\logs" mkdir "%APP_HOME%\logs"
if not defined RUNTIME_PROVIDER_TRACE set "RUNTIME_PROVIDER_TRACE=1"
if not defined RUNTIME_PROVIDER_TRACE_FILE set "RUNTIME_PROVIDER_TRACE_FILE=%APP_HOME%\logs\major-provider-trace.jsonl"
echo PROVIDER_TRACE_FILE=%RUNTIME_PROVIDER_TRACE_FILE%

echo START_COMMAND=main.py knowledge-p1-start
echo P0_DB=%P0_DB%
echo OPEN_AFTER_READY=%WEB_ENTRY%
echo STOP=Press Ctrl+C in this window
"%VENV_PY%" "%CD%\main.py" knowledge-p1-start --db "%P0_DB%" --host 127.0.0.1 --port 18080
set "RC=%ERRORLEVEL%"
echo SERVER_EXIT_CODE=%RC%
exit /b %RC%
