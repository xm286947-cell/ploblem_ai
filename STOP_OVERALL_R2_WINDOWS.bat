@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1
for %%I in ("%~dp0.") do set "PACKAGE_ROOT=%%~fI"
cd /d "%PACKAGE_ROOT%"

if exist "%PACKAGE_ROOT%\CONFIG_OVERALL_R2_WINDOWS.cmd" call "%PACKAGE_ROOT%\CONFIG_OVERALL_R2_WINDOWS.cmd"
if exist "%PACKAGE_ROOT%\.venv\Scripts\python.exe" (
  "%PACKAGE_ROOT%\.venv\Scripts\python.exe" "%PACKAGE_ROOT%\scripts\overall_r2_windows_start.py" --package-root "%PACKAGE_ROOT%" --stop
) else (
  python "%PACKAGE_ROOT%\scripts\overall_r2_windows_start.py" --package-root "%PACKAGE_ROOT%" --stop
)
set "EXIT_CODE=%ERRORLEVEL%"
if not "%OVERALL_R2_NO_PAUSE%"=="1" pause
endlocal & exit /b %EXIT_CODE%
