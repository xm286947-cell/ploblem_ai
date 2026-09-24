@echo off
setlocal
cd /d "%~dp0"

if not exist "config\runtime\model.local.yaml" (
  copy /Y "config\runtime\model.local.hardware_case.example.yaml" "config\runtime\model.local.yaml" >nul
  echo [CREATED] config\runtime\model.local.yaml
) else (
  echo [KEEP] config\runtime\model.local.yaml
)

if not exist "config\hardware_case_real_validation.local.json" (
  copy /Y "config\hardware_case_real_validation.local.example.json" "config\hardware_case_real_validation.local.json" >nul
  echo [CREATED] config\hardware_case_real_validation.local.json
) else (
  echo [KEEP] config\hardware_case_real_validation.local.json
)

for %%D in ("data" "data\input" "data\input\word" "data\tree" "data\output" "data\runtime" "data\evidence_sources" "data\hardware_case_sources") do (
  if not exist %%D mkdir %%D
)

echo.
echo Local configuration initialized.
echo 1. Edit config\runtime\model.local.yaml
echo 2. Set the API key environment variable referenced by api_key_env
echo 3. Edit config\hardware_case_real_validation.local.json for your local Word/Excel inputs
echo.
pause
endlocal
