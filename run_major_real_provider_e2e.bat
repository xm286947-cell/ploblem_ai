@echo off
setlocal
cd /d "%~dp0"

if not "%~1"=="" (
  set "MAJOR_MODEL_CONFIG=%~f1"
) else (
  if not exist "config\model.local.yaml" (
    echo [ERROR] Runtime model config not found.
    echo Usage:
    echo   run_major_real_provider_e2e.bat ^<path-to-model.local.yaml^>
    echo Or place a non-committed config\model.local.yaml in the project.
    exit /b 2
  )
)

set "MAJOR_REAL_E2E=1"
set "MAJOR_ISSUE_REAL_E2E=1"
set "MAJOR_REPEAT_REAL_E2E=1"

if not exist "test-results\major-real-provider" mkdir "test-results\major-real-provider"

where py >nul 2>nul
if %errorlevel%==0 (
  py -m pytest -q ^
    tests/test_rcfg02_major_runtime_config.py::test_rcfg02_major_occurrence_real_provider_golden_smoke ^
    tests/test_major_d01_real_provider_e2e.py::test_major_d01_real_provider_uses_runtime_model_config ^
    tests/test_repeat_real_provider_e2e.py::test_repeat_real_provider_golden_uses_runtime_model_config ^
    --junitxml=test-results/major-real-provider/golden.xml
) else (
  python -m pytest -q ^
    tests/test_rcfg02_major_runtime_config.py::test_rcfg02_major_occurrence_real_provider_golden_smoke ^
    tests/test_major_d01_real_provider_e2e.py::test_major_d01_real_provider_uses_runtime_model_config ^
    tests/test_repeat_real_provider_e2e.py::test_repeat_real_provider_golden_uses_runtime_model_config ^
    --junitxml=test-results/major-real-provider/golden.xml
)

set EXIT_CODE=%errorlevel%
echo.
if "%EXIT_CODE%"=="0" (
  echo [PASS] Major Real Provider Golden: occurrence + D01 + Repeat Decision
) else (
  echo [FAIL] Major Real Provider Golden. See pytest output and test-results\major-real-provider\golden.xml
)
endlocal & exit /b %EXIT_CODE%
