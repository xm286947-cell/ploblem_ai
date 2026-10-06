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

echo.
echo Local configuration initialized.
echo 1. Replace the safe loopback endpoint and model in config\runtime\model.local.yaml
echo 2. Set the API key environment variable if your approved provider requires one
echo 3. Edit config\hardware_case_real_validation.local.json for your local Word/Excel inputs
echo.
if not "%HARDWARE_CASE_NO_PAUSE%"=="1" pause
endlocal
