@echo off
setlocal EnableExtensions
for %%I in ("%~dp0.") do set "PACKAGE_ROOT=%%~fI"
cd /d "%PACKAGE_ROOT%"
chcp 65001 >nul 2>&1
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

if "%STORAGE_MODEL_CONFIG%"=="" set "STORAGE_MODEL_CONFIG=%PACKAGE_ROOT%\config\model.windows.real.yaml"
set "STORAGE_TEST_NO_WAIT=1"

echo ============================================================
echo STORAGE NEXT-001 - WINDOWS REAL PROVIDER VALIDATION
echo ============================================================
echo Model config : %STORAGE_MODEL_CONFIG%
echo Network owner: Unified Agent Runtime ONLY
echo API key env  : STORAGE_AGENT_API_KEY ^(only if your provider needs it^)
echo.
echo This validation will run the explicit Real Product E2E and exit.
echo Normal server startup remains: run_windows.bat
echo ============================================================
echo.

call "%PACKAGE_ROOT%\selftest_windows.bat"
set "RC=%ERRORLEVEL%"

rem Export content-safe Runtime Provider Evidence after the Real replay.
if exist "%PACKAGE_ROOT%\.venv\Scripts\python.exe" (
  "%PACKAGE_ROOT%\.venv\Scripts\python.exe" "%PACKAGE_ROOT%\scripts\export_runtime_provider_evidence.py" --db "%PACKAGE_ROOT%\.testdata\real-normal-runtime.sqlite3"
) else (
  echo [evidence] Python venv unavailable; provider evidence export skipped.
)

echo.
if "%RC%"=="0" (
  echo [PASS] Windows Real Provider validation passed.
  echo Evidence: logs\effective_runtime_config.json
  echo Provider: logs\provider_runtime.log
  echo Evidence: logs\runtime_provider_evidence_latest.txt
  echo Result  : release\PRODUCT_E2E_RESULT.json
) else (
  echo [FAIL] Windows Real Provider validation failed. exit=%RC%
  echo Start with: RUN_LOG.txt
  echo Then check : logs\diagnostic_latest.txt
  echo Provider   : logs\provider_runtime.log
  echo Evidence   : logs\runtime_provider_evidence_latest.txt
)
exit /b %RC%
