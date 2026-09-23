@echo off
setlocal
cd /d "%~dp0"
python -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
python scripts\major_case_mvp_smoke.py
exit /b %errorlevel%
