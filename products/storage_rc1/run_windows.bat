@echo off
setlocal EnableExtensions
for %%I in ("%~dp0.") do set "PACKAGE_ROOT=%%~fI"
cd /d "%PACKAGE_ROOT%"

rem P3: normal launcher is STARTUP ONLY. It must never run product E2E / mock / fault cases.
chcp 65001 >nul 2>&1
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "STORAGE_LIFE_ALLOW_UNPINNED_RUNTIME=1"

set "PACKAGE_VERSION=STORAGE_PRODUCT_MVP_RC1"
set "ROOT_LOG=%PACKAGE_ROOT%\RUN_LOG.txt"
if not exist "%PACKAGE_ROOT%\logs" mkdir "%PACKAGE_ROOT%\logs" >nul 2>&1
if not exist "%PACKAGE_ROOT%\release" mkdir "%PACKAGE_ROOT%\release" >nul 2>&1

>"%ROOT_LOG%" echo ============================================================
>>"%ROOT_LOG%" echo %PACKAGE_VERSION% - WINDOWS NORMAL STARTUP LOG
>>"%ROOT_LOG%" echo STARTED_AT=%DATE% %TIME%
>>"%ROOT_LOG%" echo PACKAGE_ROOT=%PACKAGE_ROOT%
>>"%ROOT_LOG%" echo MODE=SERVER_ONLY
>>"%ROOT_LOG%" echo PRODUCT_E2E=NOT_RUN
>>"%ROOT_LOG%" echo MOCK=NOT_RUN
>>"%ROOT_LOG%" echo FAULT_TESTS=NOT_RUN
>>"%ROOT_LOG%" echo NETWORK_OWNER=UNIFIED_AGENT_RUNTIME_ONLY
>>"%ROOT_LOG%" echo ============================================================
>>"%ROOT_LOG%" echo.

set "STORAGE_LIFE_EXECUTION_MODE=runtime"
set "UNIFIED_AGENT_RUNTIME_ROOT=%PACKAGE_ROOT%\vendor\unified_agent_runtime"
set "STORAGE_STRICT_PACKAGE_PROVENANCE=1"
if "%STORAGE_MODEL_CONFIG%"=="" (
  set "STORAGE_MODEL_CONFIG=%PACKAGE_ROOT%\config\model.local.yaml"
  set "STORAGE_MODEL_CONFIG_SOURCE=package-default"
) else (
  set "STORAGE_MODEL_CONFIG_SOURCE=user"
)
set "RUNTIME_PROVIDER_TRACE=1"
set "RUNTIME_PROVIDER_DIAGNOSTICS=1"
set "RUNTIME_PROVIDER_TRACE_FILE=%PACKAGE_ROOT%\logs\provider_runtime.log"
set "STORAGE_RUNTIME_HTTP_TRACE=1"
set "STORAGE_APP_LOG_STDOUT=1"
if "%STORAGE_WEB_HOST%"=="" set "STORAGE_WEB_HOST=0.0.0.0"
if "%STORAGE_WEB_PORT%"=="" set "STORAGE_WEB_PORT=8765"
set "STORAGE_PRODUCT_TEST_MODE=real"
if "%STORAGE_KNOWLEDGE_RELEASE_DIR%"=="" set "STORAGE_KNOWLEDGE_RELEASE_DIR=%PACKAGE_ROOT%\knowledge_release\current"

>"logs\launcher_latest.log" echo [%DATE% %TIME%] %PACKAGE_VERSION% run_windows.bat SERVER_ONLY
>"logs\provider_runtime.log" echo [trace-init] %DATE% %TIME% %PACKAGE_VERSION%

if not exist "%PACKAGE_ROOT%\.venv\Scripts\python.exe" (
  echo [setup] Creating package-local Python environment...
  >>"%ROOT_LOG%" echo [setup] Creating package-local Python environment...
  py -3 -m venv "%PACKAGE_ROOT%\.venv" >>"logs\launcher_latest.log" 2>&1
  if errorlevel 1 goto :bootstrap_failed
)

set "VIRTUAL_ENV=%PACKAGE_ROOT%\.venv"
set "PYTHONHOME="
set "PYTHONPATH=%PACKAGE_ROOT%;%UNIFIED_AGENT_RUNTIME_ROOT%"
set "PATH=%VIRTUAL_ENV%\Scripts;%PATH%"

echo [setup] Checking dependencies...
>>"%ROOT_LOG%" echo [setup] Checking dependencies...
"%PACKAGE_ROOT%\.venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r "%PACKAGE_ROOT%\requirements.txt" -r "%UNIFIED_AGENT_RUNTIME_ROOT%\requirements-runtime-p0-test.txt" >>"logs\launcher_latest.log" 2>&1
if errorlevel 1 goto :bootstrap_failed

>>"logs\launcher_latest.log" echo PACKAGE_ROOT=%PACKAGE_ROOT%
>>"logs\launcher_latest.log" echo VIRTUAL_ENV=%VIRTUAL_ENV%
>>"logs\launcher_latest.log" echo PYTHONPATH=%PYTHONPATH%
>>"logs\launcher_latest.log" echo UNIFIED_AGENT_RUNTIME_ROOT=%UNIFIED_AGENT_RUNTIME_ROOT%
>>"logs\launcher_latest.log" echo STORAGE_MODEL_CONFIG=%STORAGE_MODEL_CONFIG%
>>"logs\launcher_latest.log" echo STORAGE_MODEL_CONFIG_SOURCE=%STORAGE_MODEL_CONFIG_SOURCE%
>>"logs\launcher_latest.log" echo MODE=SERVER_ONLY

>>"%ROOT_LOG%" echo [startup] Product E2E / Mock / fault cases are NOT executed by run_windows.bat.
>>"%ROOT_LOG%" echo [startup] Explicit self-test entry: selftest_windows.bat
>>"%ROOT_LOG%" echo.

echo ============================================================
echo NORMAL STARTUP ONLY
echo Product E2E : NOT RUN
echo Mock/Fault  : NOT RUN
echo Web         : http://127.0.0.1:%STORAGE_WEB_PORT%
echo Model config: %STORAGE_MODEL_CONFIG%
echo Self-test   : selftest_windows.bat
echo ============================================================
echo.

if "%STORAGE_RELEASE_GATE_CHECK_ONLY%"=="1" (
  "%PACKAGE_ROOT%\.venv\Scripts\python.exe" "%PACKAGE_ROOT%\scripts\windows_start.py" --check-only
) else (
  "%PACKAGE_ROOT%\.venv\Scripts\python.exe" "%PACKAGE_ROOT%\scripts\windows_start.py"
)
set "RC=%ERRORLEVEL%"

>>"%ROOT_LOG%" echo.
>>"%ROOT_LOG%" echo FINISHED_AT=%DATE% %TIME%
>>"%ROOT_LOG%" echo EXIT_CODE=%RC%
copy /Y "%ROOT_LOG%" "%PACKAGE_ROOT%\logs\RUN_LOG_latest.txt" >nul 2>&1

if not "%RC%"=="0" (
  echo STARTUP FAILED. exit=%RC%
  echo Log: %ROOT_LOG%
  pause
  exit /b %RC%
)

echo Server stopped normally.
echo Log: %ROOT_LOG%
if not "%STORAGE_RELEASE_GATE_CHECK_ONLY%"=="1" pause
exit /b 0

:bootstrap_failed
>>"logs\launcher_latest.log" echo BOOTSTRAP FAILED.
>>"%ROOT_LOG%" echo BOOTSTRAP FAILED. See logs\launcher_latest.log
copy /Y "%ROOT_LOG%" "%PACKAGE_ROOT%\logs\RUN_LOG_latest.txt" >nul 2>&1
echo BOOTSTRAP FAILED. See logs\launcher_latest.log
if not "%STORAGE_RELEASE_GATE_CHECK_ONLY%"=="1" pause
exit /b 2
