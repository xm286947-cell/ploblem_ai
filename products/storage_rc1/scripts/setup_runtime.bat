@echo off
setlocal EnableExtensions EnableDelayedExpansion
set EXPECTED=f9ca45f82960b3ce380273cf26868bc842a72b7f
set PROJECT=%~dp0..
set BUNDLED=%PROJECT%\vendor\unified_agent_runtime
if not "%UNIFIED_AGENT_RUNTIME_ROOT%"=="" set CANDIDATE=%UNIFIED_AGENT_RUNTIME_ROOT%
if "%CANDIDATE%"=="" if exist "%BUNDLED%\runtime\__init__.py" set CANDIDATE=%BUNDLED%
if not "%CANDIDATE%"=="" (
  if exist "%CANDIDATE%\RUNTIME_COMMIT" (
    set /p ACTUAL=<"%CANDIDATE%\RUNTIME_COMMIT"
    if /I not "!ACTUAL!"=="%EXPECTED%" (
      echo ERROR Runtime snapshot mismatch: expected=%EXPECTED% actual=!ACTUAL! 1>&2
      exit /b 2
    )
    echo %CANDIDATE%
    exit /b 0
  )
)
if "%~1"=="" (set TARGET=%PROJECT%\.external\ploblem_ai) else (set TARGET=%~1)
if "%UNIFIED_AGENT_RUNTIME_REPO%"=="" (set REPO=https://github.com/xm286947-cell/ploblem_ai.git) else (set REPO=%UNIFIED_AGENT_RUNTIME_REPO%)
if not exist "%TARGET%\.git" (
  echo [setup] bundled Runtime not found; cloning Unified Agent Runtime -^> %TARGET% 1>&2
  git clone "%REPO%" "%TARGET%" || exit /b 2
)
for /f %%i in ('git -C "%TARGET%" rev-parse HEAD') do set ACTUAL=%%i
if /I not "!ACTUAL!"=="%EXPECTED%" (
  git -C "%TARGET%" fetch --all --tags --prune || exit /b 2
  git -C "%TARGET%" checkout --detach %EXPECTED% || exit /b 2
)
for /f %%i in ('git -C "%TARGET%" rev-parse HEAD') do set ACTUAL=%%i
if /I not "!ACTUAL!"=="%EXPECTED%" exit /b 2
echo %TARGET%
