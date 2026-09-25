@echo off
setlocal EnableExtensions
cd /d "%~dp0\.."

echo ===== Effective Runtime Config =====
if exist "logs\effective_runtime_config.json" (
  type "logs\effective_runtime_config.json"
) else (
  echo ^<not created^>
)

echo.
echo ===== Provider Network / Protocol Probe =====
if exist "logs\provider_probe.log" (
  type "logs\provider_probe.log"
) else (
  echo ^<not created^>
)

echo.
echo ===== Runtime Provider Request Trace =====
if exist "logs\provider_runtime.log" (
  type "logs\provider_runtime.log"
) else (
  echo ^<not created^>
)

echo.
echo ===== Diagnostic Summary =====
if exist "logs\diagnostic_latest.txt" (
  type "logs\diagnostic_latest.txt"
) else (
  echo ^<not created^>
)
exit /b 0
