@echo off
setlocal
cd /d "%~dp0"

if not "%~1"=="" (
  set "MAJOR_MODEL_CONFIG=%~f1"
) else (
  if not exist "config\model.local.yaml" (
    echo [ERROR] Runtime model config not found.
    echo Usage:
    echo   run_major_real_truncation_e2e.bat ^<path-to-model.local.yaml^>
    echo Or place a non-committed config\model.local.yaml in the project.
    exit /b 2
  )
)

set "MAJOR_D01_TRUNCATION_REAL_E2E=1"

if not exist "test-results\major-real-provider" mkdir "test-results\major-real-provider"

where py >nul 2>nul
if %errorlevel%==0 (
  py -m pytest -q ^
    tests/test_major_d01_real_truncation_e2e.py::test_major_d01_real_provider_truncation_recovers_by_replanning ^
    --junitxml=test-results/major-real-provider/truncation-golden.xml
) else (
  python -m pytest -q ^
    tests/test_major_d01_real_truncation_e2e.py::test_major_d01_real_provider_truncation_recovers_by_replanning ^
    --junitxml=test-results/major-real-provider/truncation-golden.xml
)

set EXIT_CODE=%errorlevel%
echo.
if "%EXIT_CODE%"=="0" (
  echo [PASS] Major D01 Real Truncation Golden
) else (
  echo [FAIL] Major D01 Real Truncation Golden. See pytest output and test-results\major-real-provider\truncation-golden.xml
)
endlocal & exit /b %EXIT_CODE%
