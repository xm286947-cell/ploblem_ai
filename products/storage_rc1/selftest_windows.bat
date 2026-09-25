@echo off
setlocal EnableExtensions
for %%I in ("%~dp0.") do set "PACKAGE_ROOT=%%~fI"
cd /d "%PACKAGE_ROOT%"

rem HOTFIX P1: force UTF-8 before any Python/Runtime/Provider work.
chcp 65001 >nul 2>&1
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "STORAGE_LIFE_ALLOW_UNPINNED_RUNTIME=1"

set "PACKAGE_VERSION=STORAGE_PRODUCT_MVP_RC1"
set "ROOT_LOG=%PACKAGE_ROOT%\RUN_LOG.txt"
if not exist "%PACKAGE_ROOT%\logs" mkdir "%PACKAGE_ROOT%\logs" >nul 2>&1
if not exist "%PACKAGE_ROOT%\release" mkdir "%PACKAGE_ROOT%\release" >nul 2>&1

>"%ROOT_LOG%" echo ============================================================
>>"%ROOT_LOG%" echo %PACKAGE_VERSION% - WINDOWS EXPLICIT PRODUCT SELFTEST LOG
>>"%ROOT_LOG%" echo STARTED_AT=%DATE% %TIME%
>>"%ROOT_LOG%" echo PACKAGE_ROOT=%PACKAGE_ROOT%
>>"%ROOT_LOG%" echo PYTHONUTF8=%PYTHONUTF8%
>>"%ROOT_LOG%" echo PYTHONIOENCODING=%PYTHONIOENCODING%
>>"%ROOT_LOG%" echo NETWORK_OWNER=UNIFIED_AGENT_RUNTIME_ONLY
>>"%ROOT_LOG%" echo NOTE=No launcher/provider probe sends HTTP requests to the Agent.
>>"%ROOT_LOG%" echo ============================================================
>>"%ROOT_LOG%" echo.

call :main >>"%ROOT_LOG%" 2>&1
set "RC=%ERRORLEVEL%"

>>"%ROOT_LOG%" echo.
>>"%ROOT_LOG%" echo ============================================================
>>"%ROOT_LOG%" echo FINISHED_AT=%DATE% %TIME%
>>"%ROOT_LOG%" echo EXIT_CODE=%RC%
>>"%ROOT_LOG%" echo ============================================================
copy /Y "%ROOT_LOG%" "%PACKAGE_ROOT%\logs\RUN_LOG_latest.txt" >nul 2>&1

echo.
echo ============================================================
echo %PACKAGE_VERSION%
echo EXIT_CODE=%RC%
echo LOG=%ROOT_LOG%
echo ============================================================
echo.
type "%ROOT_LOG%"
echo.
echo [LOG] Complete log: %ROOT_LOG%
echo.
pause
exit /b %RC%

:main
>"logs\launcher_latest.log" echo [%DATE% %TIME%] %PACKAGE_VERSION% selftest_windows.bat REAL PRODUCT E2E
>"logs\provider_runtime.log" echo [trace-init] %DATE% %TIME% %PACKAGE_VERSION%

rem REAL Agent only. Every OpenAI-compatible HTTP request must be owned by Unified Agent Runtime.
set "STORAGE_LIFE_EXECUTION_MODE=runtime"
set "UNIFIED_AGENT_RUNTIME_ROOT=%PACKAGE_ROOT%\vendor\unified_agent_runtime"
set "STORAGE_STRICT_PACKAGE_PROVENANCE=1"
if "%STORAGE_MODEL_CONFIG%"=="" (
  set "STORAGE_MODEL_CONFIG=%PACKAGE_ROOT%\config\model.local.yaml"
  set "STORAGE_MODEL_CONFIG_SOURCE=package-default"
) else (
  set "STORAGE_MODEL_CONFIG_SOURCE=user"
)
set "STORAGE_TEST_NO_WAIT=0"
if "%STORAGE_KNOWLEDGE_RELEASE_DIR%"=="" set "STORAGE_KNOWLEDGE_RELEASE_DIR=%PACKAGE_ROOT%\knowledge_release\current"
set "RUNTIME_PROVIDER_TRACE=1"
set "RUNTIME_PROVIDER_DIAGNOSTICS=1"
set "RUNTIME_PROVIDER_TRACE_FILE=%PACKAGE_ROOT%\logs\provider_runtime.log"
set "STORAGE_RUNTIME_HTTP_TRACE=1"
set "STORAGE_APP_LOG_STDOUT=1"

echo ============================================================
echo %PACKAGE_VERSION%
echo EXPLICIT SELFTEST: REAL AGENT / RUNTIME PRODUCT E2E
echo ============================================================
echo Package root : %PACKAGE_ROOT%
echo Runtime root : %UNIFIED_AGENT_RUNTIME_ROOT%
echo Model config : %STORAGE_MODEL_CONFIG%
echo Config source: %STORAGE_MODEL_CONFIG_SOURCE%
echo Network owner: Unified Agent Runtime
echo Provider trace: %RUNTIME_PROVIDER_TRACE_FILE%
echo.

if not exist "%PACKAGE_ROOT%\.venv\Scripts\python.exe" (
  echo [setup] Creating Python virtual environment...
  py -3 -m venv "%PACKAGE_ROOT%\.venv" >>"logs\launcher_latest.log" 2>&1
  if errorlevel 1 goto :bootstrap_failed
)

set "VIRTUAL_ENV=%PACKAGE_ROOT%\.venv"
set "PYTHONHOME="
set "PYTHONPATH=%PACKAGE_ROOT%;%UNIFIED_AGENT_RUNTIME_ROOT%"
set "PATH=%VIRTUAL_ENV%\Scripts;%PATH%"

echo [setup] Installing Storage + Runtime dependencies...
"%PACKAGE_ROOT%\.venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r "%PACKAGE_ROOT%\requirements.txt" -r "%UNIFIED_AGENT_RUNTIME_ROOT%\requirements-runtime-p0-test.txt" >>"logs\launcher_latest.log" 2>&1
if errorlevel 1 goto :bootstrap_failed

>>"logs\launcher_latest.log" echo PACKAGE_ROOT=%PACKAGE_ROOT%
>>"logs\launcher_latest.log" echo VIRTUAL_ENV=%VIRTUAL_ENV%
>>"logs\launcher_latest.log" echo PYTHONPATH=%PYTHONPATH%
>>"logs\launcher_latest.log" echo UNIFIED_AGENT_RUNTIME_ROOT=%UNIFIED_AGENT_RUNTIME_ROOT%
>>"logs\launcher_latest.log" echo STORAGE_LIFE_EXECUTION_MODE=%STORAGE_LIFE_EXECUTION_MODE%
>>"logs\launcher_latest.log" echo STORAGE_MODEL_CONFIG=%STORAGE_MODEL_CONFIG%
>>"logs\launcher_latest.log" echo STORAGE_MODEL_CONFIG_SOURCE=%STORAGE_MODEL_CONFIG_SOURCE%
>>"logs\launcher_latest.log" echo NETWORK_OWNER=UNIFIED_AGENT_RUNTIME_ONLY

"%PACKAGE_ROOT%\.venv\Scripts\python.exe" "%PACKAGE_ROOT%\scripts\windows_e2e.py" --mode real
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
  echo SELFTEST PRODUCT E2E FAILED. exit=%RC%
  echo Provenance evidence: %PACKAGE_ROOT%\logs\effective_runtime_config.json
  echo Runtime provider trace: %PACKAGE_ROOT%\logs\provider_runtime.log
  echo Full session log: %PACKAGE_ROOT%\logs\latest.log
  exit /b %RC%
)

echo SELFTEST PRODUCT E2E PASS.
echo Runtime provider trace: %PACKAGE_ROOT%\logs\provider_runtime.log
exit /b 0

:bootstrap_failed
>>"logs\launcher_latest.log" echo BOOTSTRAP FAILED.
>"logs\diagnostic_latest.txt" echo STORAGE PRODUCT TEST BOOTSTRAP FAILURE
>>"logs\diagnostic_latest.txt" echo See logs\launcher_latest.log
echo BOOTSTRAP FAILED.
exit /b 2
