@echo off
setlocal
cd /d "%~dp0"
echo Starting Quality Issue Analysis Engine with QualityScenario V1 preview...
echo Open http://127.0.0.1:8080/issues after startup.

if defined LEGACY_QUALITY_ISSUE_DB_PATH (
  set "QUALITY_DB=%LEGACY_QUALITY_ISSUE_DB_PATH%"
) else (
  set "QUALITY_DB=knowledge\quality_issue_v1.db"
)

echo Mature host DB: %QUALITY_DB%
if defined QUALITY_SCENARIO_V1_DB_PATH (
  echo QualityScenario V1 DB: %QUALITY_SCENARIO_V1_DB_PATH%
) else (
  echo QualityScenario V1 DB: same as mature host DB
)

set "PYTHON_CMD="
if exist ".venv\Scripts\python.exe" (
  set "PYTHON_CMD=.venv\Scripts\python.exe"
) else (
  set "BASE_PYTHON="
  where py >nul 2>nul
  if not errorlevel 1 (
    py -3.12 -c "import sys" >nul 2>nul
    if not errorlevel 1 (
      set "BASE_PYTHON=py -3.12"
    ) else (
      py -3.11 -c "import sys" >nul 2>nul
      if not errorlevel 1 set "BASE_PYTHON=py -3.11"
    )
  )
  if not defined BASE_PYTHON (
    where python3.12 >nul 2>nul
    if not errorlevel 1 set "BASE_PYTHON=python3.12"
  )
  if not defined BASE_PYTHON (
    where python3.11 >nul 2>nul
    if not errorlevel 1 set "BASE_PYTHON=python3.11"
  )
  if not defined BASE_PYTHON (
    where python >nul 2>nul
    if not errorlevel 1 set "BASE_PYTHON=python"
  )
  if not defined BASE_PYTHON (
    echo Python >= 3.11 was not found.
    exit /b 2
  )

  echo Creating isolated .venv...
  %BASE_PYTHON% -m venv .venv
  if errorlevel 1 exit /b 2
  set "PYTHON_CMD=.venv\Scripts\python.exe"
)

%PYTHON_CMD% -c "import openpyxl, fastapi, uvicorn, jinja2, pydantic, yaml, jsonschema, pypdf, pdfplumber, multipart" >nul 2>nul
if errorlevel 1 (
  echo Incomplete Python environment detected. Installing product dependencies...
  %PYTHON_CMD% -m pip install --upgrade pip
  if errorlevel 1 exit /b 2
  %PYTHON_CMD% -m pip install -r requirements.txt -r requirements-runtime-p0-test.txt
  if errorlevel 1 exit /b 2
)

%PYTHON_CMD% -c "import openpyxl; print('OPENPYXL_READY=' + openpyxl.__version__)"
if errorlevel 1 (
  echo DEPENDENCY_PREFLIGHT=FAIL
  exit /b 2
)

echo DEPENDENCY_PREFLIGHT=PASS
%PYTHON_CMD% main.py knowledge-web --db "%QUALITY_DB%" %*
set EXIT_CODE=%errorlevel%
echo.
if not "%REPEAT_CASE_NO_PAUSE%"=="1" pause
endlocal & exit /b %EXIT_CODE%
