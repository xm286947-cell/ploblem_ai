@echo off
setlocal
cd /d "%~dp0"
python -m pip install -r requirements.txt -r requirements-runtime-p0-test.txt
if errorlevel 1 exit /b 1
python scripts\hardware_case_mvp_smoke.py
exit /b %errorlevel%
