@echo off
setlocal
cd /d "%~dp0\.."
if exist "logs\effective_runtime_config.json" (
  type "logs\effective_runtime_config.json"
) else (
  echo effective_runtime_config.json not found. Run run_windows.bat first.
  exit /b 1
)
