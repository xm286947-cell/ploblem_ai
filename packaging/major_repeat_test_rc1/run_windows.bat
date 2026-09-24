@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if not exist "app\requirements.txt" (
  echo Missing app\requirements.txt
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  py -3.11 --version >nul 2>nul
  if not errorlevel 1 (
    py -3.11 -m venv ".venv"
  ) else (
    where py >nul 2>nul
    if not errorlevel 1 (
      py -3 -m venv ".venv"
    ) else (
      where python >nul 2>nul
      if errorlevel 1 (
        echo Python 3 is required.
        exit /b 1
      )
      python -m venv ".venv"
    )
  )
  if errorlevel 1 exit /b 1
  ".venv\Scripts\python.exe" -m pip install -r "app\requirements.txt"
  if errorlevel 1 exit /b 1
)
if not exist "app\config\model.yaml" (
  echo Missing app\config\model.yaml
  exit /b 1
)
cd /d "%~dp0app"
"..\.venv\Scripts\python.exe" "scripts\major_repeat_test_rc1.py" smoke
if errorlevel 1 (
  echo Package smoke test failed.
  exit /b 1
)
echo Starting existing Quality Platform Web at http://127.0.0.1:8080
"..\.venv\Scripts\python.exe" "scripts\major_repeat_test_rc1.py" serve %*
set "EXIT_CODE=%errorlevel%"
echo Web stopped. Exit code: %EXIT_CODE%
endlocal & exit /b %EXIT_CODE%
