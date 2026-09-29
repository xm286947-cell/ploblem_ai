@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if "%STORAGE_WEB_PORT%"=="" set "STORAGE_WEB_PORT=8765"
echo [Storage R1] Starting canonical Storage product on http://127.0.0.1:%STORAGE_WEB_PORT%
start "" /b cmd /c "ping 127.0.0.1 -n 4 >nul & start "" http://127.0.0.1:%STORAGE_WEB_PORT%/"
call "%~dp0run_windows.bat"
exit /b %ERRORLEVEL%
