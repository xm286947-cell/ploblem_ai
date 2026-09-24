@echo off
setlocal
cd /d "%~dp0..\app"
"..\.venv\Scripts\python.exe" "scripts\major_repeat_test_rc1.py" smoke
endlocal & exit /b %errorlevel%
