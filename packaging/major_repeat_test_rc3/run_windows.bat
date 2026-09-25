@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3.11 -m venv .venv || python -m venv .venv
  if errorlevel 1 exit /b 1
  ".venv\Scripts\python.exe" -m pip install -r app\requirements.txt
  if errorlevel 1 exit /b 1
)
cd /d "%~dp0app"
"..\.venv\Scripts\python.exe" scripts\major_repeat_test_rc3.py smoke
if errorlevel 1 exit /b 1
"..\.venv\Scripts\python.exe" scripts\major_repeat_test_rc3.py serve %*
endlocal
