@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1

for %%I in ("%~dp0.") do set "PACKAGE_ROOT=%%~fI"
cd /d "%PACKAGE_ROOT%" || exit /b 2

set "MATURE_LAUNCHER=%PACKAGE_ROOT%\start_quality_capability_p1.bat"
if not exist "%MATURE_LAUNCHER%" (
  echo MATURE_PLATFORM_LAUNCHER_MISSING=%MATURE_LAUNCHER%
  exit /b 3
)

echo PACKAGE_MODE=MATURE_PLATFORM_FOUNDATION
echo MATURE_RUNTIME_ROOT=quality_knowledge.web.app.create_app
echo DEFAULT_ENTRY=http://127.0.0.1:18080/issues
echo P0_PRODUCT_ENTRY=DISABLED
echo P0_CODE_REMOVAL=DEFERRED_UNTIL_DEPENDENCIES_ZERO

call "%MATURE_LAUNCHER%" --host 127.0.0.1 --port 18080 %*
set "EXIT_CODE=%ERRORLEVEL%"

endlocal & exit /b %EXIT_CODE%
