@echo off
setlocal EnableExtensions
cd /d "%~dp0\.."
if not exist logs mkdir logs
(
  echo STORAGE PRODUCT TEST FAILURE BUNDLE
  echo generated_at=%DATE% %TIME%
  echo.
  echo ===== logs\latest.log =====
  if exist logs\latest.log type logs\latest.log
  echo.
  echo ===== release\storage_app.log =====
  if exist release\storage_app.log type release\storage_app.log
  echo.
  echo ===== release\openai_mock.log =====
  if exist release\openai_mock.log type release\openai_mock.log
  echo.
  echo ===== release\mock_router.log =====
  if exist release\mock_router.log type release\mock_router.log
  echo.
  echo ===== release\PRODUCT_E2E_RESULT.json =====
  if exist release\PRODUCT_E2E_RESULT.json type release\PRODUCT_E2E_RESULT.json
  echo.
  echo ===== release\PRODUCT_FAILURE_E2E_RESULT.json =====
  if exist release\PRODUCT_FAILURE_E2E_RESULT.json type release\PRODUCT_FAILURE_E2E_RESULT.json
) > logs\failure_latest.txt
echo logs\failure_latest.txt
exit /b 0
