@echo off
cd /d "%~dp0"
call START_HARDWARE_CASE.bat
exit /b %errorlevel%
